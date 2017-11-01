"""
28 Aug 2013


"""
from pytadbit.modelling.imp_modelling    import generate_3d_models
from pytadbit.utils.extraviews     import plot_2d_optimization_result
from pytadbit.utils.extraviews     import plot_3d_optimization_result
from pytadbit.modelling.structuralmodels import StructuralModels
from cPickle                       import dump, load
from sys                           import stderr
import itertools
import numpy           as np
import multiprocessing as mu


class IMPoptimizer(object):
    """
    This class optimizes a set of parameters (scale, kbending, maxdist, lowfreq, and
    upfreq) in order to maximize the correlation between the contact matrix computed on
    the generted models (generated by IMP, or lammps) and the input contact map.

    :param experiment: an instance of the class pytadbit.experiment.Experiment
    :param start: first bin to model (bin number, inclusive and starting at 1)
    :param end: last bin to model (bin number)
    :param 5000 n_models: number of models to generate
    :param 1000 n_keep: number of models to use in the final analysis (usually
       the top 20% of the generated models). The models are ranked according to
       their objective function value (the lower the better)
    :param 1 close_bins: number of particles away (i.e. the bin number difference) a
       particle pair must be in order to be considered as neighbors (e.g. 1 means
       nearest neighbors particles)
    :param None cutoff: distance cutoff (nm) to define whether two particles
       are in contact or not in the models, default is 2.0 * resolution * scale.
    :param None container: restrains particle to be within a given object.
       NOTE: The container can only be a 'cylinder' of a given height closed
       by hemispheres. This cylinder is defined by its radius, its height (if
       height=0 the container is a sphere), and the strength (k) of the harmonic
       force applied in the restraint. For example, to model the confinement
       in E. coli (2 micrometers of length, and 0.5 micrometer of width),
       container = ['cylinder', 250, 1500, 50] should be used, and
       in a typical spherical mammalian nuclei (about 6 micrometers of diameter),
       container = ['cylinder', 3000, 0, 50]
    """
    def __init__(self, experiment, start, end, n_models=500,
                 n_keep=100, close_bins=1, container=None):

        (self.zscores,
         self.values, zeros) = experiment._sub_experiment_zscore(start, end)
        self.resolution = experiment.resolution
        self.zeros = tuple([i not in zeros for i in xrange(end - start + 1)])
        self.nloci = end - start + 1
        if not self.nloci == len(self.zeros):
            raise Exception('ERROR: in optimization, bad number of particles\n')
        self.n_models   = n_models
        self.n_keep     = n_keep
        self.close_bins = close_bins

        # For clarity, the order in which the optimized parameters are managed should be
        # always the same: scale, kbending, maxdist, lowfreq, upfreq
        self.scale_range    = []
        self.kbending_range = []
        self.maxdist_range  = []
        self.lowfreq_range  = []
        self.upfreq_range   = []

        self.dcutoff_range  = []

        self.container      = container
        self.results = {}


    def run_grid_search(self,
                        scale_range=0.01,
                        kbending_range=0.0,  # TODO: Choose values of kbending that should be explored by default!!!
                        maxdist_range=(400, 1500, 100),
                        lowfreq_range=(-1, 0, 0.1),
                        upfreq_range=(0, 1, 0.1),
                        dcutoff_range=2,
                        corr='spearman', off_diag=1,
                        savedata=None, n_cpus=1, verbose=True,
                        use_HiC=True, use_confining_environment=True,
                        use_excluded_volume=True):
        """
        This function calculates the correlation between the models generated
        by IMP and the input data for the four main IMP parameters (scale,
        kbending, maxdist, lowfreq and upfreq) in the given ranges of values.
        The range can be expressed as a list.

        :param n_cpus: number of CPUs to use
        :param 0.01 scale_range: upper and lower bounds used to search for
           the optimal scale parameter (unit nm per nucleotide). The last value of
           the input tuple is the incremental step for scale parameter values
        :param (0,2.0,0.5) kbending_range: values of the bending rigidity
           strength to enforce in the models
        :param (400,1400,100) maxdist_range: upper and lower bounds
           used to search for the optimal maximum experimental distance.
           The last value of the input tuple is the incremental step for maxdist
           values
        :param (-1,0,0.1) lowfreq_range: range of lowfreq values to be
           optimized. The last value of the input tuple is the incremental
           step for the lowfreq values. To be precise "freq" refers to the
           Z-score.
        :param (0,1,0.1) upfreq_range: range of upfreq values to be optimized.
           The last value of the input tuple is the incremental step for the
           upfreq values. To be precise "freq" refers to the Z-score.
        :param 2 dcutoff_range: upper and lower bounds used to search for
           the optimal distance cutoff parameter (distance, in number of beads,
           from which to consider 2 beads as being close). The last value of the
           input tuple is the incremental step for scale parameter values
        :param None savedata: concatenate all generated models into a dictionary
           and save it into a file named by this argument
        :param True verbose: print the results to the standard output
        """
        if verbose:
            stderr.write('Optimizing %s particles\n' % self.nloci)

        # These commands transform the ranges defined in input as tuples
        # in list of values to use in the grid search of the best parameters
        # scale
        if isinstance(scale_range, tuple):
            scale_step = scale_range[2]
            scale_arange = np.arange(scale_range[0],
                                          scale_range[1] + scale_step / 2,
                                          scale_step)
        else:
            if isinstance(scale_range, (float, int)):
                scale_range = [scale_range]
            scale_arange = scale_range
        # kbending
        if isinstance(kbending_range, tuple):
            kbending_step = kbending_range[2]
            kbending_arange = np.arange(kbending_range[0],
                                            kbending_range[1] + kbending_step / 2,
                                            kbending_step)
        else:
            if isinstance(kbending_range, (float, int)):
                kbending_range = [kbending_range]
            kbending_arange = kbending_range
        # maxdist
        if isinstance(maxdist_range, tuple):
            maxdist_step = maxdist_range[2]
            maxdist_arange = range(maxdist_range[0],
                                        maxdist_range[1] + maxdist_step,
                                        maxdist_step)
        else:
            if isinstance(maxdist_range, (float, int)):
                maxdist_range = [maxdist_range]
            maxdist_arange = maxdist_range
        # lowfreq
        if isinstance(lowfreq_range, tuple):
            lowfreq_step = lowfreq_range[2]
            lowfreq_arange = np.arange(lowfreq_range[0],
                                            lowfreq_range[1] + lowfreq_step / 2,
                                            lowfreq_step)
        else:
            if isinstance(lowfreq_range, (float, int)):
                lowfreq_range = [lowfreq_range]
            lowfreq_arange = lowfreq_range
        # upfreq
        if isinstance(upfreq_range, tuple):
            upfreq_step = upfreq_range[2]
            upfreq_arange = np.arange(upfreq_range[0],
                                           upfreq_range[1] + upfreq_step / 2,
                                           upfreq_step)
        else:
            if isinstance(upfreq_range, (float, int)):
                upfreq_range = [upfreq_range]
            upfreq_arange = upfreq_range
        # dcutoff
        if isinstance(dcutoff_range, tuple):
            dcutoff_step = dcutoff_range[2]
            dcutoff_arange = np.arange(dcutoff_range[0],
                                          dcutoff_range[1] + dcutoff_step / 2,
                                          dcutoff_step)
        else:
            if isinstance(dcutoff_range, (float, int)):
                dcutoff_range = [dcutoff_range]
            dcutoff_arange = dcutoff_range

        # These commands round all the values in the ranges defined as input
        # scale
        if not self.scale_range:
            self.scale_range   = [my_round(i) for i in scale_arange  ]
        else:
            self.scale_range = sorted([my_round(i) for i in scale_arange
                                       if not my_round(i) in self.scale_range] +
                                      self.scale_range)
        # scale
        if not self.kbending_range:
            self.kbending_range = [my_round(i) for i in kbending_arange]
        else:
            self.kbending_range = sorted([my_round(i) for i in kbending_arange
                                         if not my_round(i) in self.kbending_range] +
                                        self.kbending_range)
        # maxdist
        if not self.maxdist_range:
            self.maxdist_range = [my_round(i) for i in maxdist_arange]
        else:
            self.maxdist_range = sorted([my_round(i) for i in maxdist_arange
                                         if not my_round(i) in self.maxdist_range] +
                                        self.maxdist_range)
        # lowfreq
        if not self.lowfreq_range:
            self.lowfreq_range = [my_round(i) for i in lowfreq_arange]
        else:
            self.lowfreq_range = sorted([my_round(i) for i in lowfreq_arange
                                         if not my_round(i) in self.lowfreq_range] +
                                        self.lowfreq_range)
        # upfreq
        if not self.upfreq_range:
            self.upfreq_range  = [my_round(i) for i in upfreq_arange ]
        else:
            self.upfreq_range = sorted([my_round(i) for i in upfreq_arange
                                        if not my_round(i) in self.upfreq_range] +
                                       self.upfreq_range)
        # dcutoff
        if not self.dcutoff_range:
            self.dcutoff_range = [my_round(i) for i in dcutoff_arange]
        else:
            self.dcutoff_range = sorted([my_round(i) for i in dcutoff_arange
                                         if not my_round(i) in self.dcutoff_range] +
                                        self.dcutoff_range)

        # These commands perform the grid search of the best parameters
        models = {}
        count = 0
        if verbose:
            stderr.write('  %-4s%-5s\t%-8s\t%-7s\t%-7s\t%-6s\t%-7s\t%-11s\n' % (
                "num","scale","kbending","maxdist","lowfreq","upfreq","dcutoff","correlation"))
        parameters_sets = itertools.product([my_round(i) for i in scale_arange   ],
                                            [my_round(i) for i in kbending_arange],
                                            [my_round(i) for i in maxdist_arange ],
                                            [my_round(i) for i in lowfreq_arange ],
                                            [my_round(i) for i in upfreq_arange  ])


        #for (scale, maxdist, upfreq, lowfreq, kbending) in zip([my_round(i) for i in scale_arange  ],
        for (scale, kbending, maxdist, lowfreq, upfreq) in parameters_sets:
            #print (scale, kbending, maxdist, lowfreq, upfreq)

            # This check whether this optimization has been already done for this set of parameters
            if (scale, kbending, maxdist, lowfreq, upfreq) in [tuple(k[:5]) for k in self.results]:
                k = [k for k in self.results
                     if (scale, kbending, maxdist, lowfreq, upfreq) == tuple(k[:5])
                ][0]
                result = self.results[(scale, kbending, maxdist, lowfreq, upfreq, k[-1])]
                if verbose:
                    verb = '  %-5s\t%-8s\t%-7s\t%-7s\t%-6s\t%-7s\n' % (
                        'xx', scale, kbending, maxdist, lowfreq, upfreq, k[-1])

                    if verbose == 2:
                        stderr.write(verb + str(round(result, 4)) + '\n')
                    else:
                        print verb + str(round(result, 4))
                continue

            config_tmp = {'kforce'   : 5,
                          'scale'    : float(scale),
                          'kbending' : float(kbending),
                          'lowrdist' : 100, # This parameters is fixed to XXX
                          'maxdist'  : int(maxdist),
                          'lowfreq'  : float(lowfreq),
                          'upfreq'   : float(upfreq)}

            try:
                count += 1
                tdm = generate_3d_models(
                    self.zscores, self.resolution,
                    self.nloci, n_models=self.n_models,
                    n_keep=self.n_keep, config=config_tmp,
                    n_cpus=n_cpus, first=0,
                    values=self.values, container=self.container,
                    close_bins=self.close_bins, zeros=self.zeros,
                    use_HiC=use_HiC, use_confining_environment=use_confining_environment,
                    use_excluded_volume=use_excluded_volume)
                result = 0
                cutoff = my_round(dcutoff_arange[0])

                matrices = tdm.get_contact_matrix(
                    cutoff=[int(i * self.resolution * float(scale)) for i in dcutoff_arange])
                for m in matrices:
                    cut = int(m**0.5)
                    sub_result = tdm.correlate_with_real_data(cutoff=cut, corr=corr,
                                                              off_diag=off_diag,
                                                              contact_matrix=matrices[m])[0]

                    if result < sub_result:
                        result = sub_result
                        cutoff = my_round(float(cut) / self.resolution / float(scale))


            except Exception, e:
                print '  SKIPPING: %s' % e
                result = 0
                cutoff = my_round(dcutoff_arange[0])

            if verbose:
                verb = '  %-4s%-5s\t%-8s\t%-7s\t%-7s\t%-6s\t%-7s' % (
                    count, scale, kbending, maxdist, lowfreq, upfreq, cutoff)
                if verbose == 2:
                    stderr.write(verb + str(round(result, 4)) + '\n')
                else:
                    print verb + str(round(result, 4))

            # Store the correlation for the TADbit parameters set
            self.results[(scale, kbending, maxdist, lowfreq, upfreq, cutoff)] = result

            if savedata and result:
                models[(scale, kbending, maxdist, lowfreq, upfreq, cutoff)] = tdm._reduce_models(minimal=True)

        if savedata:
            out = open(savedata, 'w')
            dump(models, out)
            out.close()

        self.kbending_range.sort( key=float)
        self.scale_range.sort(  key=float)
        self.maxdist_range.sort(key=float)
        self.lowfreq_range.sort(key=float)
        self.upfreq_range.sort( key=float)
        self.dcutoff_range.sort(key=float)


    def load_grid_search_OLD(self, filenames, corr='spearman', off_diag=1,
                         verbose=True, n_cpus=1):
        """
        Loads one file or a list of files containing pre-calculated Structural
        Models (keep_models parameter used). And correlate each set of models
        with real data. Useful to run different correlation on the same data
        avoiding to re-calculate each time the models.

        :param filenames: either a path to a file or a list of paths.
        :param spearman corr: correlation coefficient to use
        'param 1 off_diag:
        :param True verbose: print the results to the standard output

        """
        if isinstance(filenames, str):
            filenames = [filenames]
        models = {}
        for filename in filenames:
            inf = open(filename)
            models.update(load(inf))
            inf.close()
        count = 0
        pool = mu.Pool(n_cpus, maxtasksperchild=1)
        jobs = {}
        for scale, maxdist, upfreq, lowfreq, dcutoff in models:
            svd = models[(scale, maxdist, upfreq, lowfreq, dcutoff)]
            jobs[(scale, maxdist, upfreq, lowfreq, dcutoff)] = pool.apply_async(
                _mu_correlate, args=(svd, corr, off_diag,
                                     scale, maxdist, upfreq, lowfreq, dcutoff,
                                     verbose, count))
            count += 1
        pool.close()
        pool.join()
        for scale, maxdist, upfreq, lowfreq, dcutoff in models:
            self.results[(scale, maxdist, upfreq, lowfreq, dcutoff)] = \
                                 jobs[(scale, maxdist, upfreq, lowfreq, dcutoff)].get()
            if not scale in self.scale_range:
                self.scale_range.append(scale)
            if not maxdist in self.maxdist_range:
                self.maxdist_range.append(maxdist)
            if not lowfreq in self.lowfreq_range:
                self.lowfreq_range.append(lowfreq)
            if not upfreq in self.upfreq_range:
                self.upfreq_range.append(upfreq)
            if not dcutoff in self.dcutoff_range:
                self.dcutoff_range.append(dcutoff)
        self.scale_range.sort(  key=float)
        self.maxdist_range.sort(key=float)
        self.lowfreq_range.sort(key=float)
        self.upfreq_range.sort( key=float)
        self.dcutoff_range.sort(key=float)


    def load_grid_search(self, filenames, corr='spearman', off_diag=1,
                         verbose=True, n_cpus=1):
        """
        Loads one file or a list of files containing pre-calculated Structural
        Models (keep_models parameter used). And correlate each set of models
        with real data. Useful to run different correlation on the same data
        avoiding to re-calculate each time the models.

        :param filenames: either a path to a file or a list of paths.
        :param spearman corr: correlation coefficient to use
        'param 1 off_diag:
        :param True verbose: print the results to the standard output

        """
        if isinstance(filenames, str):
            filenames = [filenames]
        models = {}
        for filename in filenames:
            inf = open(filename)
            models.update(load(inf))
            inf.close()
        count = 0
        pool = mu.Pool(n_cpus, maxtasksperchild=1)
        jobs = {}
        for scale, kbending, maxdist, lowfreq, upfreq, dcutoff in models:
            svd = models[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)]
            jobs[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)] = pool.apply_async(
                _mu_correlate, args=(svd, corr, off_diag,
                                     scale, kbending, maxdist, lowfreq, upfreq, dcutoff,
                                     verbose, count))
            count += 1
        pool.close()
        pool.join()
        for scale, kbending, maxdist, lowfreq, upfreq, dcutoff in models:
            self.results[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)] = \
                                 jobs[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)].get()
            if not scale in self.scale_range:
                self.scale_range.append(scale)
            if not kbending in self.kbending_range:
                self.kbending_range.append(kbending)
            if not maxdist in self.maxdist_range:
                self.maxdist_range.append(maxdist)
            if not lowfreq in self.lowfreq_range:
                self.lowfreq_range.append(lowfreq)
            if not upfreq in self.upfreq_range:
                self.upfreq_range.append(upfreq)
            if not dcutoff in self.dcutoff_range:
                self.dcutoff_range.append(dcutoff)
        self.scale_range.sort(  key=float)
        self.kbending_range.sort(key=float)
        self.maxdist_range.sort(key=float)
        self.lowfreq_range.sort(key=float)
        self.upfreq_range.sort( key=float)

        self.dcutoff_range.sort(key=float)



    def get_best_parameters_dict(self, reference=None, with_corr=False):
        """
        :param None reference: a description of the dataset optimized
        :param False with_corr: if True, returns also the correlation value

        :returns: a dict that can be used for modelling, see config parameter in
           :func:`pytadbit.experiment.Experiment.model_region`

        """
        if not self.results:
            stderr.write('WARNING: no optimization done yet\n')
            return
        best = ((float('nan'), float('nan'), float('nan'), float('nan'), float('nan'), float('nan')), 0.0)
        kbending = 0
        try:
            for (scale, maxdist, upfreq, lowfreq, kbending, cutoff), val in self.results.iteritems():
                if val > best[-1]:
                    best = ((scale, maxdist, upfreq, lowfreq, kbending, cutoff), val)
        except ValueError:
            for (scale, maxdist, upfreq, lowfreq, cutoff), val in self.results.iteritems():
                if val > best[-1]:
                    best = ((scale, maxdist, upfreq, lowfreq, kbending, cutoff), val)

        if with_corr:
            print best
            return (dict((('scale'    , float(best[0][0])),
                          ('kbending' , float(best[0][1])),
                          ('maxdist'  , float(best[0][2])),
                          ('lowfreq'  , float(best[0][3])),
                          ('upfreq'   , float(best[0][4])),
                          ('dcutoff'  , float(best[0][5])),
                          ('reference', reference or ''), ('kforce', 5))),
                    best[-1])
        else:
            return dict((('scale'    , float(best[0][0])),
                         ('kbending' , float(best[0][1])),
                         ('maxdist'  , float(best[0][2])),
                         ('lowfreq'  , float(best[0][3])),
                         ('upfreq'   , float(best[0][4])),
                         ('dcutoff'  , float(best[0][5])),
                         ('reference', reference or ''), ('kforce', 5)))


    def plot_2d_OLD(self, axes=('scale', 'maxdist', 'upfreq', 'lowfreq'),
                show_best=0, skip=None, savefig=None,clim=None):
        """
        A grid of heatmaps representing the result of the optimization.

        :param 'scale','maxdist','upfreq','lowfreq' axes: list of axes to be
           represented in the plot. The order will define which parameter will
           be placed on the x, y, z or w axe.
        :param 0 show_best: number of best correlation values to highlight in
           the plot
        :param None skip: if passed (as a dictionary), fix a given axe,
           e.g.: {'scale': 0.001, 'maxdist': 500}
        :param None savefig: path to a file where to save the image generated;
           if None, the image will be shown using matplotlib GUI (the extension
           of the file name will determine the desired format).

        """
        results = self._result_to_array()
        plot_2d_optimization_result((('scale', 'maxdist', 'upfreq', 'lowfreq'),
                                     ([float(i) for i in self.scale_range],
                                      [float(i) for i in self.maxdist_range],
                                      [float(i) for i in self.upfreq_range],
                                      [float(i) for i in self.lowfreq_range]),
                                     results), axes=axes, dcutoff=self.dcutoff_range, show_best=show_best,
                                    skip=skip, savefig=savefig,clim=clim)

    def plot_2d(self, axes=('scale', 'kbending', 'maxdist', 'lowfreq', 'upfreq'),
                show_best=0, skip=None, savefig=None,clim=None):
        """
        A grid of heatmaps representing the result of the optimization.

        :param 'scale','kbending','maxdist','lowfreq','upfreq' axes: list of
           axes to be represented in the plot. The order will define which
           parameter will be placed on the x, y, z or w axe.
        :param 0 show_best: number of best correlation values to highlight in
           the plot
        :param None skip: if passed (as a dictionary), fix a given axe,
           e.g.: {'scale': 0.001, 'maxdist': 500}
        :param None savefig: path to a file where to save the image generated;
           if None, the image will be shown using matplotlib GUI (the extension
           of the file name will determine the desired format).
        """

        results = self._result_to_array()
        plot_2d_optimization_result((('scale', 'kbending', 'maxdist', 'lowfreq', 'upfreq'),
                                     ([float(i) for i in self.scale_range],
                                      [float(i) for i in self.kbending_range],
                                      [float(i) for i in self.maxdist_range],
                                      [float(i) for i in self.lowfreq_range],
                                      [float(i) for i in self.upfreq_range]),
                                     results), dcutoff=self.dcutoff_range, axes=axes, show_best=show_best,
                                    skip=skip, savefig=savefig,clim=clim)



    def plot_3d_OLD(self, axes=('scale', 'maxdist', 'upfreq', 'lowfreq')):
        """
        A grid of heatmaps representing the result of the optimization.

        :param 'scale','maxdist','upfreq','lowfreq' axes: tuple of axes to be
           represented in the plot. The order will define which parameter will
           be placed on the x, y, z or w axe.

        """
        results = self._result_to_array()
        plot_3d_optimization_result((('scale', 'maxdist', 'upfreq', 'lowfreq'),
                                     ([float(i) for i in self.scale_range],
                                      [float(i) for i in self.maxdist_range],
                                      [float(i) for i in self.upfreq_range],
                                      [float(i) for i in self.lowfreq_range]),
                                     results), axes=axes)



    def _result_to_array_OLD(self):
        # This auxiliary method organizes the results of the grid optimization in a
        # Numerical array to be passed to the plot_2d_OLD and plot_3d functions above

        results = np.empty((len(self.scale_range), len(self.maxdist_range),
                            len(self.upfreq_range), len(self.lowfreq_range)))

        for w, scale in enumerate(self.scale_range):
            for x, maxdist in enumerate(self.maxdist_range):
                for y, upfreq in enumerate(self.upfreq_range):
                    for z, lowfreq in enumerate(self.lowfreq_range):
                        try:
                            cut = [c for c in self.dcutoff_range
                                   if (scale, maxdist, upfreq, lowfreq, c)
                                   in self.results][0]
                        except IndexError:
                            results[w, x, y, z] = float('nan')
                            continue
                        #
                        try:
                            results[w, x, y, z] = self.results[
                                (scale, maxdist, upfreq, lowfreq, cut)]
                        except KeyError:
                            results[w, x, y, z] = float('nan')
        return results



    def _result_to_array(self):
        # This auxiliary method organizes the results of the grid optimization in a
        # Numerical array to be passed to the plot_2d and plot_3d functions above

        results = np.empty((len(self.scale_range),  len(self.kbending_range),  len(self.maxdist_range),
                            len(self.lowfreq_range), len(self.upfreq_range)))

        """
        for i in xrange(len(self.scale_range)):
            for j in xrange(len(self.kbending_range)):
                for k in xrange(len(self.maxdist_range)):
                    for l in xrange(len(self.lowfreq_range)):
                        for m in xrange(len(self.upfreq_range)):
                            print "Correlation",self.scale_range[i],self.kbending_range[j],\
                            self.maxdist_range[k],self.lowfreq_range[l],self.upfreq_range[m],\
                            results[i][j][k][l][m]
        """

        for v, scale in enumerate(self.scale_range):
            for w, kbending in enumerate(self.kbending_range):
                for x, maxdist in enumerate(self.maxdist_range):
                    for y, lowfreq in enumerate(self.lowfreq_range):
                        for z, upfreq in enumerate(self.upfreq_range):

                            # Case in which there is more than 1 distance cutoff (dcutoff)
                            try:
                                cut = [c for c in self.dcutoff_range
                                       if (scale, kbending, maxdist, lowfreq, upfreq, c)
                                       in self.results][0]
                            except IndexError:
                                results[v, w, x, y, z] = float('nan')
                                continue

                            #
                            try:
                                results[v, w, x, y, z] = self.results[
                                    (scale, kbending, maxdist, lowfreq, upfreq, cut)]
                            except KeyError:
                                results[v, w, x, y, z] = float('nan')

        """
        for i in xrange(len(self.scale_range)):
            for j in xrange(len(self.kbending_range)):
                for k in xrange(len(self.maxdist_range)):
                    for l in xrange(len(self.lowfreq_range)):
                        for m in xrange(len(self.upfreq_range)):
                            print "Correlation",self.scale_range[i],self.kbending_range[j],\
                            self.maxdist_range[k],self.lowfreq_range[l],self.upfreq_range[m],\
                            results[i][j][k][l][m]
        exit(1)
        """
        return results



    def write_result(self, f_name):
        """
        This function writes a log file of all the values tested for each
        parameter, and the resulting correlation value.

        This file can be used to load or merge data a posteriori using
        the function pytadbit.modelling.impoptimizer.IMPoptimizer.load_from_file

        :param f_name: file name with the absolute path
        """
        out = open(f_name, 'w')
        out.write(('## n_models: %s n_keep: %s ' +
                   'close_bins: %s\n') % (self.n_models,
                                          self.n_keep, self.close_bins))
        out.write('# scale\tkbending\tmax_dist\tlow_freq\tup_freq\tdcutoff\tcorrelation\n')

        parameters_sets = itertools.product(*[[my_round(i) for i in self.scale_range   ],
                                              [my_round(i) for i in self.kbending_range],
                                              [my_round(i) for i in self.maxdist_range ],
                                              [my_round(i) for i in self.lowfreq_range  ],
                                              [my_round(i) for i in self.upfreq_range ]])
        for (scale, kbending, maxdist, lowfreq, upfreq) in parameters_sets:
            try:
                cut = sorted(
                    [c for c in self.dcutoff_range
                     if (scale, kbending, maxdist, lowfreq, upfreq, c)
                     in self.results],
                    key=lambda x: self.results[
                        (scale, kbending, maxdist, lowfreq, upfreq, x)])[0]
            except IndexError:
                print 'Missing dcutoff', (scale, kbending, maxdist, lowfreq, upfreq)
                continue

            try:
                result = self.results[(scale, kbending, maxdist, lowfreq, upfreq, cut)]
                out.write('  %-5s\t%-8s\t%-8s\t%-8s\t%-7s\t%-7s\t%-11s\n' % (
                    scale, kbending, maxdist, lowfreq, upfreq, cut, result))
            except KeyError:
                print 'KeyError', (scale, kbending, maxdist, lowfreq, upfreq, cut, result)
                continue
        out.close()


    def load_from_file_OLD(self, f_name):
        """
        Loads the optimized parameters from a file generated with the function:
        pytadbit.modelling.impoptimizer.IMPoptimizer.write_result.
        This function does not overwrite the parameters that were already
        loaded or calculated.

        :param f_name: file name with the absolute path
        """
        for line in open(f_name):
            # Check same parameters
            if line.startswith('##'):
                n_models, _, n_keep, _, close_bins = line.split()[2:]
                if ([int(n_models), int(n_keep), int(close_bins)]
                    !=
                    [self.n_models, self.n_keep, self.close_bins]):
                    raise Exception('Parameters does in %s not match: %s\n%s' %(
                        f_name,
                        [int(n_models), int(n_keep), int(close_bins)],
                        [self.n_models, self.n_keep, self.close_bins]))
            if line.startswith('#'):
                continue

            # OLD format before May 2017 without kbending parameter
            scale, maxdist, upfreq, lowfreq, dcutoff, result = line.split()
            # Setting the kbending to 0.0 for to be compatible with the new version
            kbending = 0.0
            scale, kbending, maxdist, lowfreq, upfreq, dcutoff = (
                float(scale), float(kbending), int(maxdist), float(lowfreq), float(upfreq),
                float(dcutoff))
            scale    = my_round(scale, val=5)
            kbending = my_round(kbending)
            maxdist  = my_round(maxdist)
            lowfreq  = my_round(lowfreq)
            upfreq   = my_round(upfreq)
            dcutoff  = my_round(dcutoff)

            self.results[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)] = float(result)
            if not scale in self.scale_range:
                self.scale_range.append(scale)
            if not kbending in self.kbending_range:
                self.kbending_range.append(kbending)
            if not maxdist in self.maxdist_range:
                self.maxdist_range.append(maxdist)
            if not lowfreq in self.lowfreq_range:
                self.lowfreq_range.append(lowfreq)
            if not upfreq in self.upfreq_range:
                self.upfreq_range.append(upfreq)
            if not dcutoff in self.dcutoff_range:
                self.dcutoff_range.append(dcutoff)

        self.scale_range.sort(   key=float)
        self.kbending_range.sort(key=float)
        self.maxdist_range.sort( key=float)
        self.lowfreq_range.sort( key=float)
        self.upfreq_range.sort(  key=float)
        self.dcutoff_range.sort( key=float)



    def load_from_file(self, f_name):
        """
        Loads the optimized parameters from a file generated with the function:
        pytadbit.modelling.impoptimizer.IMPoptimizer.write_result.
        This function does not overwrite the parameters that were already
        loaded or calculated.

        :param f_name: file name with the absolute path
        """
        for line in open(f_name):
            # Check same parameters
            if line.startswith('##'):
                n_models, _, n_keep, _, close_bins = line.split()[2:]
                if ([int(n_models), int(n_keep), int(close_bins)]
                    !=
                    [self.n_models, self.n_keep, self.close_bins]):
                    raise Exception('Parameters does in %s not match: %s\n%s' %(
                        f_name,
                        [int(n_models), int(n_keep), int(close_bins)],
                        [self.n_models, self.n_keep, self.close_bins]))
            if line.startswith('#'):
                continue
            scale, kbending, maxdist, lowfreq, upfreq, dcutoff, result = line.split()
            scale, kbending, maxdist, lowfreq, upfreq, dcutoff = (
                float(scale), float(kbending), int(maxdist), float(lowfreq), float(upfreq),
                float(dcutoff))
            scale    = my_round(scale, val=5)
            kbending = my_round(kbending)
            maxdist  = my_round(maxdist)
            lowfreq  = my_round(upfreq)
            upfreq   = my_round(lowfreq)
            dcutoff  = my_round(dcutoff)
            self.results[(scale, kbending, maxdist, lowfreq, upfreq, dcutoff)] = float(result)
            if not scale in self.scale_range:
                self.scale_range.append(scale)
            if not kbending in self.kbending_range:
                self.kbending_range.append(kbending)
            if not maxdist in self.maxdist_range:
                self.maxdist_range.append(maxdist)
            if not lowfreq in self.lowfreq_range:
                self.lowfreq_range.append(lowfreq)
            if not upfreq in self.upfreq_range:
                self.upfreq_range.append(upfreq)
            if not dcutoff in self.dcutoff_range:
                self.dcutoff_range.append(dcutoff)
        self.scale_range.sort(   key=float)
        self.kbending_range.sort(key=float)
        self.maxdist_range.sort( key=float)
        self.lowfreq_range.sort( key=float)
        self.upfreq_range.sort(  key=float)
        self.dcutoff_range.sort( key=float)



def my_round(num, val=4):
    num = round(float(num), val)
    return str(int(num) if num == int(num) else num)



def _mu_correlate(svd, corr, off_diag, scale, kbending, maxdist, lowfreq, upfreq,
                  dcutoff, verbose, count):
    tdm = StructuralModels(
        nloci=svd['nloci'], models=svd['models'],
        bad_models=svd['bad_models'],
        resolution=svd['resolution'],
        original_data=svd['original_data'],
        clusters=svd['clusters'], config=svd['config'],
        zscores=svd['zscore'])
    try:
        result = tdm.correlate_with_real_data(
            cutoff=dcutoff, corr=corr,
            off_diag=off_diag)[0]
        if verbose:
            verb = '  %-5s\t%-8s\t%-7s\t%-8s\t%-8s\t%-7s\n' % (
                scale, kbending, maxdist, lowfreq, upfreq, dcutoff)
            if verbose == 2:
                stderr.write(verb + str(result) + '\n')
            else:
                print verb + str(result)
    except Exception, e:
        print 'ERROR %s' % e
    return result
