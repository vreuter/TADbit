"""

information needed

 - path working directory with mapped reads or list of SAM/BAM/MAP files

"""

from argparse                    import HelpFormatter
from pytadbit.utils.sqlite_utils import print_db
import sqlite3 as lite
from os                          import path, remove
from string                      import ascii_letters
from random                      import random
from shutil                      import copyfile

DESC = "Describe jobs and results in a given working directory"

TABLE_IDX = {
    '1' : 'paths',
    '2' : 'jobs',
    '3' : 'mapped_outputs',
    '4' : 'mapped_inputs',
    '5' : 'parsed_outputs',
    '6' : 'intersection_outputs',
    '7' : 'filter_outputs',
    '8' : 'normalize_outputs',
    '9' : 'merge_stats',
    '10': 'merge_outputs',
    '11': 'segment_outputs',
    '12': 'models',
    '13': 'modeled_regions'}



def run(opts):
    check_options(opts)
    if 'tmpdb' in opts and opts.tmpdb:
        dbfile = opts.tmpdb
        copyfile(path.join(opts.workdir, 'trace.db'), dbfile)
    else:
        dbfile = path.join(opts.workdir, 'trace.db')
    con = lite.connect(dbfile)
    if opts.tsv and path.exists(opts.tsv):
        remove(opts.tsv)
    with con:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for table in cur.fetchall():
            if table[0].lower() in ['jobs', 'paths'] and opts.tsv:
                continue
            if table[0].lower() in opts.tables:
                print_db(cur, table[0], savedata=opts.tsv, append=True,
                         no_print=['JOBid', 'Id', 'Input',
                                   '' if table[0] == 'MAPPED_OUTPUTs'
                                   else 'PATHid'] if opts.tsv else '')
    if 'tmpdb' in opts and opts.tmpdb:
        copyfile(dbfile, path.join(opts.workdir, 'trace.db'))
        remove(dbfile)


def populate_args(parser):
    """
    parse option from call
    """
    parser.formatter_class = lambda prog: HelpFormatter(prog, width=95,
                                                        max_help_position=27)

    glopts = parser.add_argument_group('General options')

    glopts.add_argument('-w', '--workdir', dest='workdir', metavar="PATH",
                        action='store', default=None, type=str,
                        help='''path to working directory (generated with the
                        tool tadbit mapper)''')

    glopts.add_argument('-t', '--table', dest='tables', metavar='',
                        action='store', nargs='+', type=str,
                        default=[str(t) for t in range(1, len(TABLE_IDX) + 1)],
                        help=('[%(default)s] what tables to show, wrte either '
                              'the sequence of names or indexes, according to '
                              'this list: {}').format(', '.join(
                                  ['%s: %s' % (k, v)
                                   for k, v in TABLE_IDX.iteritems()])))

    glopts.add_argument('--tmpdb', dest='tmpdb', action='store', default=None,
                        metavar='PATH', type=str,
                        help='''if provided uses this directory to manipulate the
                        database''')

    glopts.add_argument('--tsv', dest='tsv', action='store', default=None,
                        metavar='PATH', type=str,
                        help='''store output in tab separated format to the
                        provided path.''')

    parser.add_argument_group(glopts)


def check_options(opts):
    if not opts.workdir:
        raise Exception('ERROR: output option required.')

    choices = reduce(lambda x, y: x + y,
                     [kv for kv in sorted(TABLE_IDX.iteritems(),
                                          key=lambda x: int(x[0]))])
    
    recovered = []
    bads = []
    for t in range(len(opts.tables)):
        opts.tables[t] = opts.tables[t].lower()
        if not opts.tables[t] in choices:
            # check if the begining of the input string matches any of
            # the possible choices
            found = False
            for choice in TABLE_IDX.values():
                if choice.startswith(opts.tables[t]):
                    recovered.append(choice)
                    found = True
            if not found:
                print(('error: argument -t/--table: invalid choice: %s'
                       '(choose from %s )') % (opts.tables[t], str(choices)))
                exit()
            bads.append(t)
        opts.tables[t] = TABLE_IDX.get(opts.tables[t], opts.tables[t])
    for bad in bads[::-1]:
        del(opts.tables[bad])
    for rec in recovered:
        opts.tables.append(rec)

    if 'tmpdb' in opts and opts.tmpdb:
        dbdir = opts.tmpdb
        # tmp file
        dbfile = 'trace_%s' % (''.join([ascii_letters[int(random() * 52)]
                                        for _ in range(10)]))
        opts.tmpdb = path.join(dbdir, dbfile)
