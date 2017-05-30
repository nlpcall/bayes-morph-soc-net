"""Implement agent-based social network model of Bayesian learning of morph."""

# from collections import OrderedDict  # TODO(RJR) probably unnecessary
from collections import Counter
import logging as lg
from math import e
from math import factorial
from math import floor
from math import gamma
from math import lgamma
from math import log
from math import log1p
import random
from statistics import mean
import time

import entropy as malouf
import mesa
import mesa.datacollection
import mesa.time
import numpy as np
import pandas as pd
from scipy.stats import entropy as KL_div  # KL_div(real_dist, estim_dist)


def connect(gen_size, gen_count, nx_gen, **kwargs):
    """Generate connections between agents."""
    import networkx as nx
    if nx_gen is None:
        nx_gen = nx.fast_gnp_random_graph
    if kwargs == {}:
        kwargs = {'p': 0.5}
    kwargs['n'] = gen_size * 2  # override input value for network size
    lg.info('Building networks with {}...'.format(nx_gen.__name__))
    lg.info('    kwargs: '.format(kwargs))
    er_networks = [[]] * gen_size  # 1st gen does not have connections
    for i in range(gen_count - 1):
        er = nx_gen(**kwargs)
        relabel_dict = {j: j + (i * gen_size) for j in er}
        er = nx.relabel_nodes(er, relabel_dict)
        er_networks.extend(er.adjacency_list()[:gen_size])
    # TODO(RJR) ensure that all nodes have at least one connection?
    return er_networks


def Ndict2pdict(in_dict):
    """Transform raw count dict to proportional dict."""
    N = sum(in_dict.values())
    return {k: v / N for k, v in in_dict.items()}


def Nlist2plist(in_list):
    """Transform raw count list to proportional list."""
    N = sum(in_list)
    return [i / N for i in in_list]


def dicts2dict(list_of_dicts):
    """Enforce coherence of lexeme by averaging probs across conditions."""
    if list_of_dicts == []:
        return {}
    if len(list_of_dicts) == 1:
        return list_of_dicts[0]
    key_set = set()
    for d in list_of_dicts:
        for k in d:
            key_set.add(k)
    return {k: mean([d.get(k, 0.0) for d in list_of_dicts]) for k in key_set}


def key_of_highest_value(in_dict):
    """Return the key with the highest value."""
    return max(in_dict.items(), key=lambda x: x[1])[0]


def prob_output(in_dict):
    """Return a random key, choice weighted by value.

    Given {0: 0.1, 1: 0.9} 1 has a 90% chance of being selected.
    """
    keys = list(in_dict.keys())
    return random.choices(keys, weights=[in_dict[k] for k in keys])


def product(iterable):
    """Compute the multiplicative product of elements in an iterable."""
    p = 1
    for i in iterable:
        p *= i
    return p


def factorial_log(in_int, log_func=log):  # deprecated in favor of lgamma(n+1)
    """Compute factorial in log-space."""
    return sum([log_func(i) for i in range(1, in_int+1)])


def mlt_prob(p_dict, d_dict):  # multinomial distribution
    """Return probability of observing d_dict assuming p_dict."""
    try:
        return ((factorial(sum(d_dict.values())) /
                 (product([factorial(i) for i in d_dict.values()]))) *
                (product([p_dict[e] ** d_dict.get(e, 0) for e in p_dict])))
    except OverflowError:
        lg.warn('    mlt_prob had OverflowError! '
                'Backing off to mlt_prob_log...')
        return mlt_prob_log(p_dict, d_dict)


def mlt_prob_log(p_dict, d_dict):  # multinomial distribution
    """Probability of observing d_dict (data) assuming p_dict (prob dist)."""
    # try:
    return ((lgamma(sum(d_dict.values()) + 1) -
             (sum([lgamma(i + 1) for i in d_dict.values()]))) +
            (sum([log(p) * d_dict.get(e, 0) for e, p in p_dict.items()])))
           # NB(RJR) This returns log(p)!! For p, e**log(p)
    # except ValueError:
    #     lg.warn('    mlt_prob_log threw ValueError! '
    #             'Backing off to mlt_prob_log1p...')
    #     return mlt_prob_log1p(p_dict, d_dict)


def mlt_prob_log1p(p_dict, d_dict):  # multinomial distribution
    """Return probability of observing d_dict assuming p_dict."""
    return ((lgamma(sum(d_dict.values()) + 1) -
             (sum([lgamma(i + 1) for i in d_dict.values()]))) +
            (sum([log1p(p) * d_dict.get(e, 0) for e, p in p_dict.items()])))
           # NB(RJR) This returns log(p)!! For p, e**log(p)

def homogen(model):
    """Return a model's homogeneity."""
    agent_morphs = [a.morphology for a in model.schedule.agents
                    if a.RIP is False]
    a_count = len([m for m in agent_morphs if m == 'a'])
    b_count = len([m for m in agent_morphs if m == 'b'])
    try:
        return a_count / b_count
    except ZeroDivisionError:
        return 0


def lex_size(agent):
    """Return agent's morphology."""
    try:
        return len(agent.l_dist)
    except AttributeError:
        return len(list(agent.model.seed_lexemes()))


def stars_and_bins(n, k, the_list=[]):
    """Distribute n probability tokens among k endings.

    Generator implementation of the stars-and-bars algorithm.
    I use 'bins' instead of dividing 'bars': bins=bars+1

    Arguments:
    n   --  number of probability tokens (stars)
    k   --  number of endings (bins)
    """
    if n == 0:
        yield the_list + [0] * k
    elif k == 1:
        yield the_list + [n]
    else:
        for i in range(n + 1):
            yield from stars_and_bins(n - i, k - 1, the_list + [i])


def gen_hyp_space(list_of_items, increment_divisor=None):
    """Generate list of dicts filling the hypothesis space.

    Each dict is of the form ...
    {i1: 0.0, i2: 0.1, i3: 0.0, ...}
    ... where .values() sums to 1.

    Arguments:
    list_of_items     -- items that receive prior weights
    increment_divisor -- Increment by 1/increment_divisor. For example,
                         4 yields (0.0, 0.25, 0.5, 0.75, 1.0).
    """
    _LEN = len(list_of_items)
    multiplier = 10000  # used to minimize effect of plus-one smoothing
    if increment_divisor is None:
        increment_divisor = _LEN
    new_divisor = increment_divisor * multiplier + _LEN
    for perm in stars_and_bins(increment_divisor, _LEN):
        perm = [(s * multiplier + 1) / new_divisor for s in perm]
        yield dict([(list_of_items[i], perm[i]) for i in range(_LEN)])


class MorphAgent(mesa.Agent):
    """An agent to teach/learn a morphological grammar."""

    def __init__(self, unique_id, model, gen_id):
        """Initialize MorphAgent object."""
        super().__init__(unique_id, model)
        self.gen_id = gen_id
        self.is_adult = False  # False=child, True=adult
        self.RIP = False  # True=moved on to the great model in the sky
        self.morphology = ''
        self.connections = set(model.network[self.unique_id])
        self.input = []   # Input obtained from adult agents
        self.ddist = {}

    def __hash__(self):
        """Define MorphAgent's __hash__ for sorting."""
        return hash((self.RIP, self.is_adult, self.unique_id))

    def step(self):
        """Take this action when called upon by the model's schedule."""
        lg.info('Agent {:>5} is '
                'stepping...(gen:{:>2})'.format(self.unique_id, self.gen_id))
        if self.is_adult and not self.RIP:  # 'active' adults
            lg.info('    retiring...')
            self.RIP = True
            # TODO(RJR) delete massive memory attributes (h-space?)
        elif (not self.is_adult and
              self.model.schedule.steps == self.gen_id - 1):  # active children
            lg.info('    retrieving input...')
            lg.info('    connections: {}'.format(self.connections))
            new_input = [a.speak() for a in self.model.schedule.agents[:]
                         if a.is_adult and
                         a.unique_id in self.connections and
                         a.gen_id != self.gen_id]
            for i in new_input:
                self.input.extend(i)
            self.input = sorted(self.input)
            # inputs = []
            # for a in self.model.schedule.agents[:]:
            #     if a.is_adult:
            #         if a.unique_id in self.connections:
            #             inputs.append(a.morphology)
            if self.input == []:
                lg.warn('Input is empty!!!')
            lg.info('    processing input...')
            self.process_input()  # Process input and generate output
            self.is_adult = True
        elif (not self.is_adult and
              self.model.schedule.steps != self.gen_id - 1):
            lg.info('    still unborn.')
        elif self.RIP:
            # raise RuntimeError('Agent {:>5} is already '
            #                    'retired.'.format(self.unique_id))
            lg.info('    already retired.'.format(self.unique_id))
        else:
            raise RuntimeError('Something strange with agent '
                               '{:>5}.'.format(self.unique_id))

    def process_input(self):
        """Do something interesting, but Bayesian."""
        # data distribution
        lg.info('    compiling data distribution...')
        self.l_dist = Counter([i[0] for i in self.input])  # freq of lexemes
        self.l_p_dist = Ndict2pdict(self.l_dist)
        self.ms_dist = Counter([i[1] for i in self.input])  # freq of t_MSPSs
        self.ms_p_dist = Ndict2pdict(self.ms_dist)
        self.ddist = {}  # ddist[(l, t_ms, g_ms, g_e)] = {e1: x, e2: y, ...}
        for l, t_ms, t_e, g_ms, g_e in self.input:
            # if (l, t_ms, g_ms, g_e) in ddist:
            #     if t_e in ddist[(l, t_ms, g_ms, g_e)]:
            #         ddist[(l, t_ms, g_ms, g_e)][t_e] += 1
            #     else:
            #         ddist[(l, t_ms, g_ms, g_e)][t_e] = 1
            # else:
            #     ddist[(l, t_ms, g_ms, g_e)] = {t_e: 1}
            try:
                self.ddist[(l, t_ms, g_ms, g_e)][t_e] += 1
            except KeyError:
                try:
                    self.ddist[(l, t_ms, g_ms, g_e)][t_e] = 1
                except KeyError:
                    self.ddist[(l, t_ms, g_ms, g_e)] = {t_e: 1}
        # Perform Bayesian learning
        lg.info('    learning Bayesian posteriors...')
        self.learn()
        # transform counts to probabilities
        # lexical dictionary
        # lex_dict[(l, t_ms)] = {e1: p1, e2: p2, ...}
        lg.info('    compiling lex_dict...')
        # l_list = sorted(list(set([(l, t_ms) for l, t_ms, g_ms, g_e in ddist])))  # noqa
        lg.info('        compiling list of unique lexemes...')
        l_list = sorted(list(set([k[0] for k in self.post_dist])))
        lg.info('        compiling the dictionary...')
        lex_dict = {l:{} for l in l_list}
        for (l, t_ms, g_ms, g_e), e_dist in self.post_dist.items():
            try:
                lex_dict[l][t_ms].append(e_dist)
            except KeyError:
                lex_dict[l][t_ms] = [e_dist]
        for l in lex_dict:
            for t_ms in lex_dict[l]:
                lex_dict[l][t_ms] = dicts2dict(lex_dict[l][t_ms])
        # for l in l_list:
        #     lex_dict[l] = {}
        #     for t_ms in self.model.seed_MSPSs:
        #         t_p_dict = dicts2dict([v for k, v in self.post_dist.items()
        #                                if k[:2] == (l, t_ms)])
        #         lex_dict[l][t_ms] = t_p_dict
        lg.info('        lexeme count: {}'.format(len(lex_dict)))
        self.in_lex_dict = lex_dict
        lg.info('    (not) generating table from lex_dict...')  # TODO(RJR)!!!!
        # table_dict = {}
        # for lex, l_dict in self.in_lex_dict.items():
        #     lg.debug('      {}, {}'.format(lex, l_dict))
        #     e_list = tuple([self.model.out_func(l_dict[m])
        #                     for m in self.model.seed_MSPSs])
        #     try:
        #         table_dict[e_list] += 1
        #     except KeyError:
        #         table_dict[e_list] = 1
        # d = {m: pd.Series([each[0][i] for each in sorted(table_dict.items(),
        #                                                  key=lambda x: x[1],
        #                                                  reverse=True)],
        #                   index=[each[1] for each in sorted(table_dict.items(),
        #                                                     key=lambda x: x[1],
        #                                                     reverse=True)])
        #      for i, m in enumerate(self.model.seed_MSPSs)}
        # self.morph_table = pd.DataFrame(d)
        # lg.info('    table is...{}'.format(self.morph_table))
        lg.info('    ...done!')

    def prior(self, MNBs, t_ms, g_ms, g_e, h_dist):
        """Compute prior probability."""
        # TODO(RJR) NB! KL_div is not symmetric! Order matters
        # First seq should be the "real" distribution
        # Second seq should be the "sample" distribution
        return KL_div(h_dist, [MNBs[(t_ms, g_ms, g_e)][i]
                               for i in sorted(MNBs[(t_ms, g_ms, g_e)])])

    def likelihood(self, lex, t_ms, g_ms, g_e, h_dist):
        """Compute the likelihood: p(D | h)."""
        return KL_div(h_dist, self.ddist[(lex, t_ms, g_ms, g_e)])

    def learn(self):
        """Determine hypothesis/hypotheses with highest posterior prob."""
        lg.info('        compiling mean neighbor behaviors...')
        # lg.info('            compiling sets of possible flections for each MSPS...')
        # flections = {}
        # for f_ms in self.ms_dist:
        #     for (l, t_ms, g_ms, g_e), e_dist in self.ddist.items():
        #         try:
        #             flections[g_ms].add(g_e)
        #         except AttributeError:
        #             flection[g_ms] = set([g_e])
        lg.info('            compiling MNBs')
        MNBs = {}
        for l, t_ms, t_e, g_ms, g_e in self.input:
            try:
                MNBs[(t_ms, g_ms, g_e)].update([t_e])
            except KeyError:
                MNBs[(t_ms, g_ms, g_e)] = Counter([t_e])
        for k in MNBs:
            MNBs[k] = dict(MNBs[k])
        # for mnb_t_ms in self.ms_dist:
        #     for mnb_g_ms in self.ms_dist:
        #         if mnb_t_ms != mnb_g_ms:
        #             # flections = [g_e for (l, t_ms, g_ms, g_e), e_dist
        #             #              in self.ddist.items() if g_ms == mnb_g_ms]
        #             lg.info('            compiling list MNBs')
        #             for f in flections[mnb_g_ms]:
        #                 MNBs[(mnb_t_ms, mnb_g_ms, f)] = dict(Counter(
        #                     [t_e for l, t_ms, t_e, g_ms, g_e in self.input
        #                      if t_ms == mnb_t_ms and g_ms == mnb_g_ms and
        #                      g_e == f]))
        lg.info('        compiling posteriors dictionary...')
        self.post_dist = {}
        for (l, t_ms, g_ms, g_e), ddist_e_dist in self.ddist.items():
            mnb_e_dist = MNBs.get((t_ms, g_ms, g_e), {})
            max_h = []
            for h in gen_hyp_space(sorted(list(mnb_e_dist)),
                                   increment_divisor=self.model.h_space_incr):
                if mnb_e_dist != {}:
                    prior = mlt_prob_log(h, mnb_e_dist)
                else:
                    prior = 0.0  # log(1)  # TODO(RJR) bad logic?
                if ddist_e_dist != {}:
                    likelihood = mlt_prob_log(h, ddist_e_dist)
                else:
                    likelihood = 0.0  # log(1)  # TODO(RJR) logic?
                posterior = prior + likelihood  # TODO(RJR) add weights?
                if max_h != [] and posterior < max_h[0][1]:
                    continue
                elif max_h == [] or posterior > max_h[0][1]:
                    max_h = [(h, posterior)]
                elif posterior == max_h[0][1]:
                    max_h.append((h, posterior))
            if len(max_h) > 1:
                lg.warn('        {} hypotheses have the '
                        'same (max) posterior!: {} {}'.format(len(max_h),
                                                              mnb_e_dist,
                                                              ddist_e_dist))
                for t in max_h:
                    lg.warn('        {}'.format(t))
                # average hypotheses with same probability
                max_h = dicts2dict([i[0] for i in max_h])
            else:
                max_h = max_h[0][0]
            self.post_dist[(l, t_ms, g_ms, g_e)] = max_h

    def speak(self):
        """Generate output.

        Each datum is a tuple in the following format:
        (lexeme, target_msps, target_ending, given_msps, given_ending)

        Return a list of tuples.
        """
        out = []
        if self.input == [] and self.gen_id == 0:  # 1st generation
            out_lexemes = list(self.model.seed_lexemes())
            out_l_weights = [i[2] for i in out_lexemes]
            out_lexemes = [i[:2] for i in out_lexemes]
            for out_l in random.choices(out_lexemes,
                                        weights=out_l_weights,
                                        k=self.model.prod_size):
                t_ms = random.choice(self.model.seed_MSPSs)  # TODO(RJR) weights?  # noqa
                g_ms = random.choice(list(set(self.model.seed_MSPSs) - {t_ms}))
                out.append((out_l[1],
                            t_ms,
                            self.model.seed_infl_classes[out_l[0]][t_ms],
                            g_ms,
                            self.model.seed_infl_classes[out_l[0]][g_ms]))
        else:
            out_lexemes = list(self.l_p_dist)
            out_l_weights = [self.l_p_dist[l] for l in out_lexemes]
            for out_l in random.choices(out_lexemes,
                                        weights=out_l_weights,
                                        k=self.model.prod_size):
                t_ms = random.choice(self.model.seed_MSPSs)  # TODO(RJR) wghts?
                try:
                    t_e = self.model.out_func(self.in_lex_dict[out_l][t_ms])
                except ValueError:
                    continue
                except KeyError:
                    continue
                g_ms = random.choice(list(set(self.model.seed_MSPSs) - {t_ms}))
                try:
                    g_e = self.model.out_func(self.in_lex_dict[out_l][g_ms])
                except ValueError:
                    continue
                except KeyError:
                    continue
                out.append((out_l, t_ms, t_e, g_ms, g_e))
        return out


class MorphLearnModel(mesa.Model):
    """A multi-generation model with some number of agents."""

    def __init__(self, *, gen_size=50, gen_count=10, morph_filename=None,
                 nw_func=None, nw_kwargs={}, discrete=True, whole_lex=True,
                 h_space_increment=None, lexeme_count=1000, zipf_max=100,
                 prod_size=100):
        """Initialize model object.

        Arguments:
        gen_size  -- number of agents per generation
        gen_count -- number of generations to simulate
        morph_filename  -- filename of tab-sep table: msps x classes
        nw_func   -- One of the following types...
            list     -- e.g. list[0] is list of connected nodes
            function -- function to generate adjacency list
            A function must be accompanied by nw_kwargs!
        nw_kwargs -- dict of arguments for nw_func function
        discrete -- boolean: discretize probability before productions
        whole_lex -- boolean: use whole lexicon to calculate priors
                     ...or else a random sampling of neighbors.
        h_space_increment -- int: Denominator for calculating the
                             granularity with which to fill the
                             hypothesis space, e.g. value of 5 leads to
                             increments of 0.2 (1/5). If unspecified,
                             defaults to the maximum number of
                             inflectional endings per MSPS in the seed
                             morphology. # TODO(RJR) allow dynamic increments?
        lexeme_count -- Number of lexemes in seed morphology
        zipf_max -- Basis for generating token frequencies
        prod_size -- How many productions each agent should 'speak'.
        """
        lg.info('Initializing model...')
        self.step_timesteps = [time.time()]
        self.num_agents = gen_size * gen_count
        lg.info('    gen_size: {}'.format(gen_size))
        self.gen_size = gen_size
        lg.info('    gen_count: {}'.format(gen_count))
        self.gen_count = gen_count
        self.parse_seed_morph(morph_filename)
        if h_space_increment is None:
            self.h_space_incr = self.max_flections
        else:
            self.h_space_incr = h_space_increment
        self.lexeme_zipf_const = 1
        while self.lexeme_dist_count(self.lexeme_zipf_const) < lexeme_count:
            self.lexeme_zipf_const += 1
        self.lexeme_type_freq_list = [floor(self.lexeme_zipf_const/i)
                                      for i in
                                      range(1, len(self.seed_MSPSs) + 1)]
        self.zipf_max = zipf_max
        self.prod_size = prod_size
        # try:
        self.network = connect(gen_size, gen_count, nw_func, **nw_kwargs)
        # except TypeError:
        #     try:
        #         assert isinstance(nw_func, list)
        #     except AssertionError:
        #         raise AssertionError('nw_func must be either a function or '
        #                              'an adjacency list.')
        #     self.network = nw_func
        lg.info('=' * 79)
        lg.info('Network adjacency list:')
        for i, j in enumerate(self.network):
            lg.info('    {:>4} => {}'.format(i, j))
        assert self.num_agents == len(self.network)
        self.discrete = discrete
        if self.discrete:
            self.out_func = key_of_highest_value
        else:
            self.out_func = prob_output
        self.whole_lex = whole_lex
        self.schedule = mesa.time.BaseScheduler(self)

        # Create agents
        lg.info('Generating agents...')
        gen_counter = 0
        for i in range(self.num_agents):
            if i % self.gen_size == 0 and i > 0:
                gen_counter += 1
            a = MorphAgent(i, self, gen_counter)
            if i < self.gen_size:  # The 1st generation are adults
                a.is_adult = True
            self.schedule.add(a)

        # Data collectors
        self.dc = mesa.datacollection.DataCollector(
            model_reporters={'Homogen': homogen},
            agent_reporters={'Lexicon_size': lex_size})

    def lexeme_dist_count(self, constant):
        """Return total number of lexemes given a constant for zipfian dist."""
        return sum([floor(constant/i)
                    for i in range(1, len(self.seed_MSPSs) + 1)])

    def parse_seed_morph(self, input_filename):
        """Build seed morphology from a file.

        The first row contains headers, 'typeFreq' followed by MSPSs:
        typeFreq    MSPS1   MSPS2   MSPS3   etc.    ...

        Each following row represents an inflection class:
        457         a       a       i       etc.    ...
        12          a       i       i       etc.    ...
        ...
        ...
        """
        # TODO(RJR) Make all of this part of a Seed object?
        self.seed_filename = input_filename
        with open(input_filename) as in_file:
            self.seed_cols = [c for c
                              in in_file.readline().rstrip().split('\t')]
            self.seed_MSPSs = self.seed_cols[1:]
            self.seed_infl_classes = []  # list of dicts
            for line in in_file:
                infl_class = {}
                for i, value in enumerate(line.rstrip().split('\t')):
                    if self.seed_cols[i] == 'typeFreq':
                        infl_class[self.seed_cols[i]] = int(value)
                    else:
                        try:
                            infl_class[self.seed_cols[i]] = float(value)
                        except ValueError:
                            infl_class[self.seed_cols[i]] = value
                self.seed_infl_classes.append(infl_class)
        self.max_type_freq = max([c['typeFreq']
                                  for c in self.seed_infl_classes])
        flections = {}
        self.seed_msps_dict = {}
        for msps in self.seed_MSPSs:
            flections[msps] = set()
            self.seed_msps_dict[msps] = {}
            for ic in self.seed_infl_classes:
                flections[msps].add(ic[msps])
                try:
                    self.seed_msps_dict[msps][ic[msps]] += ic['typeFreq']
                except KeyError:
                    self.seed_msps_dict[msps][ic[msps]] = ic['typeFreq']
        self.max_flections = max([len(s) for k, s in flections.items()])
        self.seed_mnb_dict = {}
        for target_msps in self.seed_MSPSs:
            for given_msps in self.seed_MSPSs:
                if target_msps != given_msps:
                    for given_ending in self.seed_msps_dict[given_msps]:
                        tgg = (target_msps, given_msps, given_ending)
                        self.seed_mnb_dict[tgg] = self.seed_MNBs(*tgg)

    def seed_MNBs(self, target_msps, given_msps, given_ending):
        """Calculate type-freq-weighted prevalence of endings.

        Given an MSPS and its ending, return the probability of each
        ending for a particular MSPS, e.g...

        NomSg | (AccSg = -u) -> {ø: 0.0, a: 1.0, o: 0.0}

        Return dictionary of ending:probability pairs.
        """
        out_dict = {}
        for i in self.seed_infl_classes:
            if i[given_msps] == given_ending:
                try:
                    out_dict[i[target_msps]] += i['typeFreq']
                except KeyError:
                    out_dict[i[target_msps]] = i['typeFreq']
        return Ndict2pdict(out_dict)

    def MNBs(self, lex_dict, target_msps, given_msps, given_ending):
        """Calculate type-freq-weighted prevalence of endings.

        Given an MSPS and its ending, return the probability of each
        ending for a particular MSPS, e.g...

        NomSg | (AccSg = -u) -> {ø: 0.0, a: 1.0, o: 0.0}

        Return dictionary of ending:probability pairs.
        """
        out_dict = {}
        for l, d in lex_dict.items():
            if self.out_func(d[given_msps]) == given_ending:
                try:
                    out_dict[self.out_func(d[target_msps])] += 1
                except KeyError:
                    out_dict[self.out_func(d[target_msps])] = 1
        return Ndict2pdict(out_dict)

    def seed_lexemes(self):
        """Deterministic lexeme generator.

        output -- tuple(inflection_class, lexeme, tok_freq)
        """
        for ci, c in enumerate(self.seed_infl_classes):
            for i in range(self.lexeme_type_freq_list[ci]):  # each lexeme is named ci-i
                # generate tok_freq based on zipfian dist, chopping off tail
                for tok_freq in [floor(self.zipf_max / i)
                                 for i in range(1, c['typeFreq'] + 1)]:
                    yield (ci, '{}-{}'.format(ci, i), tok_freq)

    def step(self):
        """Advance the model by one step."""
        lg.info('Model is stepping...')
        self.dc.collect(self)  # collect data
        self.schedule.step()
        self.step_timesteps.append(time.time())
