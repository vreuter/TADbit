"""
10 nov. 2014

iterative mapping copied from hiclib

"""

import os, sys
import tempfile
import subprocess
import gzip
import pysam
import gem

def parse_sam(fnam):
    """
    """
    reads1 = {}
    reads2 = {}
    nreads = {1:{}, 2:{}}
    for read in [1, 2]:
        reads = reads1 if read == 1 else reads2
        for clen in range(20, 45, 5):
            print 'loading length:', clen
            for chunk in range(1, 3):
                fnam = 'results_0/dmel_%s.txt.%s.%s' % (read, chunk, clen)
                print ' ->', fnam
                try:
                    fhandler = pysam.Samfile(fnam)
                except IOError:
                    continue
                for r in fhandler:
                    if r.is_unmapped:
                        continue
                    if r.tags[1][1]!=1:
                        continue
                    positive = not r.is_reverse
                    seq = r.seq
                    qname = r.qname.split('.')[1]
                    reads[qname] = (fhandler.getrname(r.tid), 
                                    r.pos + (0 if positive else len(seq)),
                                    positive, seq)
            nreads[read].setdefault(clen, 0)
            nreads[read][clen] += len(reads)
    return reads1, reads2


def trimming(raw_seq_len, seq_start, min_seq_len):
    return seq_start, raw_seq_len - seq_start - min_seq_len


def iterative_mapping(gem_index_path, fastq_path, out_sam_path,
                      range_start, range_stop, **kwargs):
    """
    :param fastq_path: 152 bases first 76 from one end, next 76 from the other
       end. Both to be read from left to right.
    """
    gem_index_path      = os.path.abspath(os.path.expanduser(gem_index_path))
    fastq_path          = os.path.abspath(os.path.expanduser(fastq_path))
    out_sam_path        = os.path.abspath(os.path.expanduser(out_sam_path))
    single_end          = kwargs.get('single_end'          , False)
    nthreads            = kwargs.get('nthreads'            , 4)
    max_edit_distance   = kwargs.get('max_edit_distance'   , 0.04)
    mismatches          = kwargs.get('mismatches'          , 0.04)
    nthreads            = kwargs.get('nthreads'            , 4)
    max_reads_per_chunk = kwargs.get('max_reads_per_chunk', -1)
    temp_dir = os.path.abspath(os.path.expanduser(
        kwargs.get('temp_dir', tempfile.gettempdir())))

    #get the length of a read
    fastqh = open(fastq_path)
    raw_seq_len = int(fastqh.next().strip().split('length=')[1])
    fastqh.close()

    # Split input files if required and apply iterative mapping to each
    # segment separately.
    if max_reads_per_chunk > 0:
        kwargs['max_reads_per_chunk'] = -1
        print 'Split input file %s into chunks' % fastq_path
        chunked_files = _chunk_file(
            fastq_path,
            os.path.join(temp_dir, os.path.split(fastq_path)[1]),
            max_reads_per_chunk * 4)
        print '%d chunks obtained' % len(chunked_files)
        for i, fastq_chunk_path in enumerate(chunked_files):
            print 'Run iterative_mapping recursively on %s' % fastq_chunk_path
            iterative_mapping(
                gem_index_path, fastq_chunk_path,
                out_sam_path + '.%d' % (i + 1), range_start[:], range_stop[:],
                **kwargs)

            # Delete chunks only if the file was really chunked.
            if len(chunked_files) > 1:
                print 'Remove the chunks: %s' % ' '.join(chunked_files)
                os.remove(fastq_chunk_path)
        return

    # end position according to sequence in the file
    try:
        seq_end = range_stop.pop(0)
        seq_beg = range_start.pop(0)
    except IndexError:
        return

    # define what we trim
    seq_len = seq_end - seq_beg
    trim_5, trim_3 = trimming(raw_seq_len, seq_beg, seq_len)

    # output
    local_out_sam = out_sam_path + '.%d' % (seq_len)
    # input
    inputf = gem.files.open(fastq_path)

    # trimming
    trimmed = gem.filter.run_filter(
        inputf, ['--hard-trim', '%d,%d' % (trim_5, trim_3)],
        threads=nthreads, paired=not single_end)
    
    # mapping
    mapped = gem.mapper(trimmed, gem_index_path, min_decoded_strata=0,
                        max_decoded_matches=2, unique_mapping=False,
                        max_edit_distance=max_edit_distance,
                        mismatches=mismatches,
                        output=temp_dir + '/test.map',
                        threads=nthreads)

    # convert to sam
    sam = gem.gem2sam(mapped, index=gem_index_path, output=local_out_sam,
                      threads=nthreads, single_end=single_end)

    # Check if the next iteration is required.
    if not range_stop:
        return

    # Recursively go to the next iteration.
    unmapped_fastq_path = os.path.join(
        temp_dir, os.path.split(fastq_path)[1] + '.%d' % seq_len)
    _filter_unmapped_fastq(fastq_path, local_out_sam, unmapped_fastq_path)

    iterative_mapping(gem_index_path, unmapped_fastq_path,
                      out_sam_path,
                      range_start, range_stop, **kwargs)

    os.remove(unmapped_fastq_path)

def _line_count(path):
    '''Count the number of lines in a file. The function was posted by
    Mikola Kharechko on Stackoverflow.
    '''

    f = open(path)
    lines = 0
    buf_size = 1024 * 1024
    read_f = f.read  # loop optimization

    buf = read_f(buf_size)
    while buf:
        lines += buf.count('\n')
        buf = read_f(buf_size)

    return lines

def _chunk_file(in_path, out_basename, max_num_lines):
    '''Slice lines from a large file.
    The line numbering is as in Python slicing notation.
    '''
    num_lines = _line_count(in_path)
    if num_lines <= max_num_lines:
        return [in_path, ]

    out_paths = []

    for i, line in enumerate(open(in_path)):
        if i % max_num_lines == 0:
            out_path = out_basename + '.%d' % (i // max_num_lines + 1)
            out_paths.append(out_path)
            out_file = file(out_path, 'w')
        out_file.write(line)

    return out_paths

def _filter_fastq(ids, in_fastq, out_fastq):
    '''Filter FASTQ sequences by their IDs.

    Read entries from **in_fastq** and store in **out_fastq** only those
    the whose ID are in **ids**.
    '''
    out_file = open(out_fastq, 'w')
    in_file = _gzopen(in_fastq)
    while True:
        line = in_file.readline()
        if not line:
            break

        if not line.startswith('@'):
            raise Exception(
                '{0} does not comply with the FASTQ standards.'.format(in_fastq))

        fastq_entry = [line, in_file.readline(),
                       in_file.readline(), in_file.readline()]
        read_id = line.split()[0][1:]
        if read_id.endswith('/1') or read_id.endswith('/2'):
            read_id = read_id[:-2]
        if read_id in ids:
            out_file.writelines(fastq_entry)


def _filter_unmapped_fastq(in_fastq, in_sam, nonunique_fastq):
    '''Read raw sequences from **in_fastq** and alignments from
    **in_sam** and save the non-uniquely aligned and unmapped sequences
    to **unique_sam**.
    '''
    samfile = pysam.Samfile(in_sam)

    nonunique_ids = set()
    for read in samfile:
        tags_dict = dict(read.tags)
        read_id = read.qname
        # If exists, the option 'XS' contains the score of the second
        # best alignment. Therefore, its presence means non-unique alignment.
        if 'XS' in tags_dict or read.is_unmapped or (
            'NH' in tags_dict and int(tags_dict['NH']) > 1):
            nonunique_ids.add(read_id)
            
        # UNMAPPED reads should be included 5% chance to be mapped
        # with larger fragments, so do not do this:
        # if 'XS' in tags_dict or (
        #     'NH' in tags_dict and int(tags_dict['NH']) > 1):
        #     nonunique_ids.add(read_id)

    _filter_fastq(nonunique_ids, in_fastq, nonunique_fastq)

def _gzopen(path):
    if path.endswith('.gz'):
        return gzip.open(path)
    else:
        return open(path)
