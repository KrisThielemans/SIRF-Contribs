#
# SPDX-License-Identifier: Apache-2.0
#
# Classes implementing the BSREM algorithm in sirf.STIR
#
# Authors:  Kris Thielemans
#
# Copyright 2024 University College London

import numpy
import sirf.STIR as STIR
from sirf.Utilities import examples_data_path

from cil.optimisation.algorithms import Algorithm 

class BSREMSkeleton(Algorithm):
    ''' Main implementation of a modified BSREM algorithm

    This essentially implements constrained preconditioned gradient ascent
    with an EM-type preconditioner
    '''
    def __init__(self, data, initial, initial_step_size, relaxation_eta,
                 iteration_filter=STIR.TruncateToCylinderProcessor(), **kwargs):
        '''
        iteration_filter is applied after every update. Set it to `None` if you don't want any.
        '''
        super().__init__(**kwargs)
        self.x = initial.copy()
        self.data = data
        self.num_subsets = len(data)
        self.initial_step_size = initial_step_size
        self.relaxation_eta = relaxation_eta
        # compute small number to add to image in preconditioner
        # don't make it too small as otherwise the algorithm cannot recover from zeroes.
        self.eps = initial.max()/1e3
        self.average_sensitivity = initial.get_uniform_copy(0)
        for s in range(len(data)):
            self.average_sensitivity += self.subset_sensitivity(s)/self.num_subsets
        # add a small number to avoid division by zero in the preconditioner
        self.average_sensitivity += self.average_sensitivity.max()/1e4
        self.subset = 0
        self.iteration_filter = iteration_filter
        self.configured = True

    def subset_sensitivity(self, subset_num):
        raise NotImplementedError

    def subset_gradient(self, x, subset_num):
        raise NotImplementedError

    def epoch(self):
        return self.iteration // self.num_subsets

    def step_size(self):
        return self.initial_step_size / (1 + self.relaxation_eta * self.epoch())

    def update(self):
        g = self.subset_gradient(self.x, self.subset)
        self.x_update = (self.x + self.eps) * g / self.average_sensitivity * self.step_size()
        if self.iteration_filter:
            self.iteration_filter.apply(self.x_update)
        self.x += self.x_update
        # threshold to non-negative
        self.x.maximum(0, out=self.x)
        self.subset = (self.subset + 1) % self.num_subsets

    def update_objective(self):
        # required for current CIL (needs to set self.loss)
        self.loss.append(self.objective_function(self.x))

    def objective_function(self, x):
        ''' value of objective function summed over all subsets '''
        v = 0
        for s in range(len(self.data)):
            v += self.subset_objective(x, s)
        return v

    def subset_objective(self, x, subset_num):
        ''' value of objective function for one subset '''
        raise NotImplementedError

class BSREM1(BSREMSkeleton):
    ''' BSREM implementation using sirf.STIR objective functions'''
    def __init__(self, data, obj_funs, initial, initial_step_size=1, relaxation_eta=0, **kwargs):
        '''
        construct Algorithm with lists of data and, objective functions, initial estimate, initial step size,
        step-size relaxation (per epoch) and optionally Algorithm parameters
        '''
        self.obj_funs = obj_funs
        super().__init__(data, initial, initial_step_size, relaxation_eta, **kwargs)

    def subset_sensitivity(self, subset_num):
        ''' Compute sensitivity for a particular subset'''
        self.obj_funs[subset_num].set_up(self.x)
        # note: sirf.STIR Poisson likelihood uses `get_subset_sensitivity(0) for the whole
        # sensitivity if there are no subsets in that likelihood
        return self.obj_funs[subset_num].get_subset_sensitivity(0)

    def subset_gradient(self, x, subset_num):
        ''' Compute gradient at x for a particular subset'''
        return self.obj_funs[subset_num].gradient(x)

    def subset_objective(self, x, subset_num):
        ''' value of objective function for one subset '''
        return self.obj_funs[subset_num](x)

class BSREM2(BSREMSkeleton):
    ''' BSREM implementation using acquisition models and prior'''
    def __init__(self, data, acq_models, prior, initial, initial_step_size=1, relaxation_eta=0, **kwargs):
        '''
        construct Algorithm with lists of data and acquisition models, prior, initial estimate, initial step size,
        step-size relaxation (per epoch) and optionally Algorithm parameters.

        WARNING: This version will use the same prior in each subset without rescaling. You should
        therefore rescale the penalisation_factor of the prior before calling this function. This will
        change in the future.
        '''
        self.acq_models = acq_models
        self.prior = prior
        super().__init__(data, initial, initial_step_size, relaxation_eta, **kwargs)

    def subset_sensitivity(self, subset_num):
        ''' Compute sensitivity for a particular subset'''
        self.acq_models[subset_num].set_up(self.data[subset_num], self.x)
        return self.acq_models[subset_num].backward(self.data[subset_num].get_uniform_copy(1))

    def subset_gradient(self, x, subset_num):
        ''' Compute gradient at x for a particular subset'''
        f = self.acq_models[subset_num].forward(x)
        quotient = self.data[subset_num] / f
        return self.acq_models[subset_num].backward(quotient - 1) - self.prior.gradient(x)

    def subset_objective(self, x, subset_num):
        ''' value of objective function for one subset '''
        f = self.acq_models[subset_num].forward(x)
        return self.data[subset_num].dot(f.log()) - f.sum() - self.prior(x)

