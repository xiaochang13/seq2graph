# -*- coding: utf-8 -*-

'''This module contains recurrent network structures.'''

import collections
import numpy as np
import re
import sys
import theano.tensor as TT
import theano
import pdb

from . import feedforward


def batches(samples, labels=None, steps=100, batch_size=64, rng=None):
    '''Return a callable that generates samples from a dataset.

    Parameters
    ----------
    samples : ndarray (time-steps, data-dimensions)
        An array of data. Rows in this array correspond to time steps, and
        columns to variables.
    labels : ndarray (time-steps, label-dimensions), optional
        An array of data. Rows in this array correspond to time steps, and
        columns to labels.
    steps : int, optional
        Generate samples of this many time steps. Defaults to 100.
    batch_size : int, optional
        Generate this many samples per call. Defaults to 64. This must match the
        batch_size parameter that was used when creating the recurrent network
        that will process the data.
    rng : :class:`numpy.random.RandomState` or int, optional
        A random number generator, or an integer seed for a random number
        generator. If not provided, the random number generator will be created
        with an automatically chosen seed.

    Returns
    -------
    callable :
        A callable that can be used inside a dataset for training a recurrent
        network.
    '''
    if rng is None or isinstance(rng, int):
        rng = np.random.RandomState(rng)

    def unlabeled_sample():
        xs = np.zeros((steps, batch_size, samples.shape[1]), samples.dtype)
        for i in range(batch_size):
            j = rng.randint(len(samples) - steps)
            xs[:, i, :] = samples[j:j+steps]
        return [xs]

    def labeled_sample():
        xs = np.zeros((steps, batch_size, samples.shape[1]), samples.dtype)
        ys = np.zeros((steps, batch_size, labels.shape[1]), labels.dtype)
        for i in range(batch_size):
            j = rng.randint(len(samples) - steps)
            xs[:, i, :] = samples[j:j+steps]
            ys[:, i, :] = labels[j:j+steps]
        return [xs, ys]

    return unlabeled_sample if labels is None else labeled_sample


class Text(object):
    '''A class for handling sequential text data.

    Parameters
    ----------
    text : str
        A blob of text.
    alpha : str, optional
        An alphabet to use for representing characters in the text. If not
        provided, all characters from the text occurring at least ``min_count``
        times will be used.
    min_count : int, optional
        If the alphabet is to be computed from the text, discard characters that
        occur fewer than this number of times. Defaults to 2.
    unknown : str, optional
        A character to use to represent "out-of-alphabet" characters in the
        text. This must not be in the alphabet. Defaults to '\0'.

    Attributes
    ----------
    text : str
        A blob of text, with all non-alphabet characters replaced by the
        "unknown" character.
    alpha : str
        A string containing each character in the alphabet.
    '''

    def __init__(self, text, alpha=None, min_count=2, unknown='\0'):
        self.alpha = alpha
        if self.alpha is None:
            self.alpha = ''.join(sorted(set(
                char for char, count in
                collections.Counter(text).items()
                if char != unknown and count >= min_count)))
        self.text = re.sub(r'[^{}]'.format(re.escape(self.alpha)), unknown, text)
        assert unknown not in self.alpha
        self._rev_index = unknown + self.alpha
        self._fwd_index = dict(zip(self._rev_index, range(1 + len(self.alpha))))

    def encode(self, txt):
        '''Encode a text string by replacing characters with alphabet index.

        Parameters
        ----------
        txt : str
            A string to encode.

        Returns
        -------
        classes : list of int
            A sequence of alphabet index values corresponding to the given text.
        '''
        return list(self._fwd_index.get(c, 0) for c in txt)

    def decode(self, enc):
        '''Encode a text string by replacing characters with alphabet index.

        Parameters
        ----------
        classes : list of int
            A sequence of alphabet index values to convert to text.

        Returns
        -------
        txt : str
            A string containing corresponding characters from the alphabet.
        '''
        return ''.join(self._rev_index[c] for c in enc)

    def classifier_batches(self, time_steps, batch_size, rng=None):
        '''Create a callable that returns a batch of training data.

        Parameters
        ----------
        time_steps : int
            Number of time steps in each batch.
        batch_size : int
            Number of training examples per batch.
        rng : :class:`numpy.random.RandomState` or int, optional
            A random number generator, or an integer seed for a random number
            generator. If not provided, the random number generator will be
            created with an automatically chosen seed.

        Returns
        -------
        batch : callable
            A callable that, when called, returns a batch of data that can be
            used to train a classifier model.
        '''
        assert batch_size >= 2, 'batch_size must be at least 2!'

        if rng is None or isinstance(rng, int):
            rng = np.random.RandomState(rng)

        def batch():
            inputs = np.zeros((time_steps, batch_size, 1 + len(self.alpha)), 'f')
            outputs = np.zeros((time_steps, batch_size), 'i')
            for b in range(batch_size):
                offset = rng.randint(len(self.text) - time_steps - 1)
                enc = self.encode(self.text[offset:offset + time_steps + 1])
                inputs[np.arange(time_steps), b, enc[:-1]] = 1
                outputs[np.arange(time_steps), b] = enc[1:]
            return [inputs, outputs]

        return batch


_warned = False


def _warn_dimshuffle():
    global _warned
    if not _warned:
        sys.stderr.write('''\
*****  WARNING: In theanets 0.7.0, recurrent models will use a  *****
*****  new axis ordering! Learn more at http://goo.gl/kXB4Db    *****
''')
        _warned = True


class Autoencoder(feedforward.Autoencoder):
    '''An autoencoder network attempts to reproduce its input.

    A recurrent autoencoder model requires the following inputs during training:

    - ``x``: A three-dimensional array of input data. Each element of axis 0 of
      ``x`` is expected to be one moment in time. Each element of axis 1 of
      ``x`` represents a single data sample in a batch of samples. Each element
      of axis 2 of ``x`` represents the measurements of a particular input
      variable across all times and all data items.
    '''

    def _setup_vars(self, sparse_input):
        '''Setup Theano variables for our network.

        Parameters
        ----------
        sparse_input : bool
            Not used -- sparse inputs are not supported for recurrent networks.

        Returns
        -------
        vars : list of theano variables
            A list of the variables that this network requires as inputs.
        '''
        _warn_dimshuffle()

        assert not sparse_input, 'Theanets does not support sparse recurrent models!'

        # the first dimension indexes time, the second indexes the elements of
        # each minibatch, and the third indexes the variables in a given frame.
        self.x = TT.tensor3('x')

        # the weights are the same shape as the output and specify the strength
        # of each entries in the error computation.
        self.weights = TT.tensor3('weights')

        if self.weighted:
            return [self.x, self.weights]
        return [self.x]


class Predictor(Autoencoder):
    '''A predictor network attempts to predict its next time step.

    A recurrent prediction model takes the following inputs:

    - ``x``: A three-dimensional array of input data. Each element of axis 0 of
      ``x`` is expected to be one moment in time. Each element of axis 1 of
      ``x`` represents a single sample in a batch of data. Each element of axis
      2 of ``x`` represents the measurements of a particular input variable
      across all times and all data items.
    '''

    def error(self, outputs):
        '''Build a theano expression for computing the network error.

        Parameters
        ----------
        outputs : dict mapping str to theano expression
            A dictionary of all outputs generated by the layers in this network.

        Returns
        -------
        error : theano expression
            A theano expression representing the network error.
        '''
        # we want the network to predict the next time step. if y is the output
        # of the network and f(y) gives the prediction, then we want f(y)[0] to
        # match x[1], f(y)[1] to match x[2], and so forth.
        err = self.x[1:] - self.generate_prediction(outputs)[:-1]
        if self.weighted:
            return (self.weights[1:] * err * err).sum() / self.weights[1:].sum()
        return (err * err).mean()

    def generate_prediction(self, outputs):
        '''Given outputs from each time step, map them to subsequent inputs.

        This defaults to the identity transform, i.e., the output from one time
        step is treated as the input to the next time step with no
        transformation. Override this method in a subclass to provide, e.g.,
        predictions based on random samples, lookups in a dictionary, etc.

        Parameters
        ----------
        outputs : dict mapping str to theano expression
            A dictionary of all outputs generated by the layers in this network.

        Returns
        -------
        prediction : theano variable
            A symbolic variable representing the inputs for the next time step.
        '''
        return outputs[self.output_name()]


class Regressor(feedforward.Regressor):
    '''A regressor attempts to produce a target output.

    A recurrent regression model takes the following inputs:

    - ``x``: A three-dimensional array of input data. Each element of axis 0 of
      ``x`` is expected to be one moment in time. Each element of axis 1 of
      ``x`` holds a single sample from a batch of data. Each element of axis 2
      of ``x`` represents the measurements of a particular input variable across
      all times and all data items.

    - ``targets``: A three-dimensional array of target output data. Each element
      of axis 0 of ``targets`` is expected to be one moment in time. Each
      element of axis 1 of ``targets`` holds a single sample from a batch of
      data. Each element of axis 2 of ``targets`` represents the measurements of
      a particular output variable across all times and all data items.
    '''

    def _setup_vars(self, sparse_input):
        '''Setup Theano variables for our network.

        Parameters
        ----------
        sparse_input : bool
            Not used -- sparse inputs are not supported for recurrent networks.

        Returns
        -------
        vars : list of theano variables
            A list of the variables that this network requires as inputs.
        '''
        _warn_dimshuffle()

        assert not sparse_input, 'Theanets does not support sparse recurrent models!'

        # the first dimension indexes time, the second indexes the elements of
        # each minibatch, and the third indexes the variables in a given frame.
        self.x = TT.tensor3('x')

        # for a regressor, this specifies the correct outputs for a given input.
        self.targets = TT.tensor3('targets')

        # the weights are the same shape as the output and specify the strength
        # of each entries in the error computation.
        self.weights = TT.tensor3('weights')

        if self.weighted:
            return [self.x, self.targets, self.weights]
        return [self.x, self.targets]


class Classifier(feedforward.Classifier):
    '''A classifier attempts to match a 1-hot target output.

    Unlike a feedforward classifier, where the target labels are provided as a
    single vector, a recurrent classifier requires a vector of target labels for
    each time step in the input data. So a recurrent classifier model requires
    the following inputs for training:

    - ``x``: A three-dimensional array of input data. Each element of axis 0 of
      ``x`` is expected to be one moment in time. Each element of axis 1 of
      ``x`` holds a single sample in a batch of data. Each element of axis 2 of
      ``x`` represents the measurements of a particular input variable across
      all times and all data items in a batch.

    - ``labels``: A two-dimensional array of integer target labels. Each element
      of ``labels`` is expected to be the class index for a single batch item.
      Axis 0 of this array represents time, and axis 1 represents data samples
      in a batch.
    '''

    def _setup_vars(self, sparse_input):
        '''Setup Theano variables for our network.

        Parameters
        ----------
        sparse_input : bool
            Not used -- sparse inputs are not supported for recurrent networks.

        Returns
        -------
        vars : list of theano variables
            A list of the variables that this network requires as inputs.
        '''
        _warn_dimshuffle()

        assert not sparse_input, 'Theanets does not support sparse recurrent models!'

        self.src = TT.ftensor3('src')
        #self.src_mask = TT.imatrix('src_mask')
        self.src_mask = TT.matrix('src_mask')
        self.dst = TT.ftensor3('dst')
        self.labels = TT.imatrix('labels')
        self.weights = TT.matrix('weights')

        if self.weighted:
            return [self.src, self.src_mask, self.dst, self.labels, self.weights]
        return [self.src, self.dst]

    def feed_forward(self, src, src_mask, dst, **kwargs):
        key = self._hash(**kwargs)
        if key not in self._functions:
            outputs, updates = self.build_graph(**kwargs)
            labels, exprs = list(outputs.keys()), list(outputs.values())
            self._functions[key] = (
                labels,
                theano.function([self.src,self.src_mask, self.dst], exprs, updates=updates),
        )
        labels, f = self._functions[key]
        return dict(zip(labels, f(src,src_mask, dst)))

    def error(self, outputs):
        '''Build a theano expression for computing the network error.

        Parameters
        ----------
        outputs : dict mapping str to theano expression
            A dictionary of all outputs generated by the layers in this network.

        Returns
        -------
        error : theano expression
            A theano expression representing the network error.
        '''
        output = outputs[self.output_name()]
        alpha = outputs['hid2:alpha']
        alpha_sum = alpha.sum(axis = 0) # max_dst_len * batch_size * max_src_len
        alpha_l_inf = alpha_sum.max(axis = -1) # batch_size

        # flatten all but last components of the output and labels
        n = output.shape[0] * output.shape[1]
        
        #print output.shape.eval()
        correct = TT.reshape(self.labels, (n, ))
        weights = TT.reshape(self.weights, (n, ))
        prob = TT.reshape(output, (n, output.shape[2]))
        nlp = -TT.log(TT.clip(prob[TT.arange(n), correct], 1e-8, 1))
        if self.weighted:
            return (weights * nlp).sum() / weights.sum() +  alpha_l_inf.mean()
        return nlp.mean()

    def predict_captions_forward_batch(self, x_src, mask_src, beam_size = 20, **kwargs):
        batch_size = x_src.shape[1]
        y = []
        # Important! 0 is the start token.
        batch_of_beams = [ [(0.0, [0])] for i in range(batch_size)]
        nsteps = 0
        word_num = self.layers[-1].size

        while True:
            beam_c = [[] for i in range(batch_size) ]
            idx_prevs = [ [] for i in range(batch_size)]
            idx_of_idx = [[] for i in range(batch_size)]
            idx_of_idx_len = [ ]

            max_b = -1
            cnt_ins = 0
            for i in range(batch_size):
                beams = batch_of_beams[i]
                for k, b in enumerate(beams):
                    idx_prev = b[-1]
                    if idx_prev[-1] == 1:
                        beam_c[i].append(b)
                        continue

                    idx_prevs[i].append( idx_prev)
                    idx_of_idx[i].append(k) # keep the idx for future track.
                    idx_of_idx_len.append(len(idx_prev))
                    cnt_ins += 1
                    if len(idx_prev) > max_b:
                        max_b = len(idx_prev)
            if cnt_ins == 0:
                # we do not need the 20 steps, now we have find a total of $beam_size$ candidates. just break.
                break
            x_i = np.zeros((max_b, cnt_ins, word_num), dtype='float32')
            x_src_i = np.zeros((x_src.shape[0], cnt_ins, x_src.shape[2]), dtype='float32')
            mask_src_i = np.zeros((mask_src.shape[0], cnt_ins), dtype='float32')
            idx_base = 0
            for j,idx_prev_j in enumerate(idx_prevs):
                for m, idx_prev in enumerate(idx_prev_j):
                    for k in range(len(idx_prev)):
                        x_i[k, m + idx_base, idx_prev[k]] = 1.0
                # This may be potentially error? When one batch or one image is empty (have already generated 20 sentences.
                #v_i[idx_base:idx_base + len(idx_prev_j),:] = img_fea[j,:]
                x_src_i[:,idx_base:idx_base+len(idx_prev_j),:] = x_src[:,j:j+1,:] # just make np happy
                mask_src_i[:,idx_base:idx_base+len(idx_prev_j)] = mask_src[:,j:j+1] # just make np happy
                idx_base += len(idx_prev_j)

            network_pred = self.feed_forward(x_src_i, mask_src_i, x_i, **kwargs)
            p = np.zeros((network_pred['out'].shape[1], network_pred['out'].shape[2]))
            for i in range(network_pred['out'].shape[1]):
                p[i,:] = network_pred['out'][idx_of_idx_len[i]-1,i,:]
            l = np.log( 1e-20 + p)
            top_indices = np.argsort( -l, axis=-1)
            idx_base = 0
            for batch_i, idx_i in enumerate(idx_of_idx):
                for j,idx in enumerate(idx_i):
                    row_idx = idx_base + j
                    for m in range(beam_size):
                        wordix = top_indices[row_idx][m]
                        beam_c[batch_i].append((batch_of_beams[batch_i][idx][0] + l[row_idx][wordix], batch_of_beams[batch_i][idx][1] + [wordix]))
                idx_base += len(idx_i)
            for i in range(len(beam_c)):
                beam_c[i].sort(reverse = True) # descreasing order.
            for i, b in enumerate(beam_c):
                batch_of_beams[i] = beam_c[i][:beam_size]
            nsteps += 1
            if nsteps >= 20:
                break
        for beams in batch_of_beams:
            pred = [(b[0], b[1]) for b in beams ]
            y.append(pred)
        return y 

    def predict_sequence(self, seed, steps, streams=1, rng=None):
        '''Draw a sequential sample of classes from this network.

        Parameters
        ----------
        seed : list of int
            A list of integer class labels to "seed" the classifier.
        steps : int
            The number of time steps to sample.
        streams : int, optional
            Number of parallel streams to sample from the model. Defaults to 1.
        rng : :class:`numpy.random.RandomState` or int, optional
            A random number generator, or an integer seed for a random number
            generator. If not provided, the random number generator will be
            created with an automatically chosen seed.

        Yields
        ------
        label(s) : int or list of int
            Yields at each time step an integer class label sampled sequentially
            from the model. If the number of requested streams is greater than
            1, this will be a list containing the corresponding number of class
            labels.
        '''
        if rng is None or isinstance(rng, int):
            rng = np.random.RandomState(rng)
        start = len(seed)
        batch = max(2, streams)
        inputs = np.zeros((start + steps, batch, self.layers[0].size), 'f')
        inputs[np.arange(start), :, seed] = 1
        for i in range(start, start + steps):
            chars = []
            for pdf in self.predict_proba(inputs[:i])[-1]:
                try:
                    c = rng.multinomial(1, pdf).argmax(axis=-1)
                except ValueError:
                    # sometimes the pdf triggers a normalization error. just
                    # choose greedily in this case.
                    c = pdf.argmax(axis=-1)
                chars.append(int(c))
            inputs[i, np.arange(batch), chars] = 1
            yield chars[0] if streams == 1 else chars
