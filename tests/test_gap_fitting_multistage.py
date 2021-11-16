import shutil
import json
import os
import re

from pathlib import Path
from xml.etree import cElementTree

import pytest

from wfl.configset import ConfigSet_in
from wfl.fit.gap_multistage import fit

params = {
    "stages": [
        {
            "error_scale_factor": 10.0,
            "descriptors": [
                {
                    "descriptor": {
                        "distance_Nb": True,
                        "order": 2, "cutoff": 2.5, "cutoff_transition_width": 0.51, "compact_clusters": True,
                        "Z": [5, 5]
                    },
                    "fit": {
                        "n_sparse": 15, "covariance_type": "ard_se", "theta_uniform": 0.51, "sparse_method": "uniform",
                        "f0": 0.0
                    },
                    "count_cutoff": 2.4,
                    "add_species": False
                }
            ]
        },
        {
            "error_scale_factor": 1.0,
            "descriptors": [
                {
                    "descriptor": {
                        "soap": True, "n_max": 12, "l_max": 3, "atom_sigma": 0.64, "cutoff": 2.6,
                        "cutoff_transition_width": 0.64,
                        "central_weight": 1.0, "Z": 5, "n_species": 1, "species_Z": [5]
                    },
                    "fit": {
                        "n_sparse": 100, "f0": 0.0, "covariance_type": "dot_product", "zeta": 4,
                        "sparse_method": "cur_points",
                        "print_sparse_index": True
                    },
                    "add_species": False
                },
                {
                    "descriptor": {
                        "soap": True, "n_max": 12, "l_max": 3, "atom_sigma": 0.96, "cutoff": 3.8,
                        "cutoff_transition_width": 0.96,
                        "central_weight": 1.0, "Z": 5, "n_species": 1, "species_Z": [5]
                    },
                    "fit": {
                        "n_sparse": 100, "f0": 0.0, "covariance_type": "dot_product", "zeta": 4,
                        "sparse_method": "cur_points",
                        "print_sparse_index": True
                    },
                    "add_species": False
                }
            ]
        }
    ],
    "gap_params": {"default_sigma": [0.0025, 0.0625, 0.125, 0.125], "sparse_jitter": 1.0e-8, "do_copy_at_file": False,
                   "sparse_separate_file": True}
}


@pytest.mark.skipif(not shutil.which("gap_fit"), reason="gap_fit not in PATH")  # skips it if gap_fit not in path
def test_gap_multistage_fit(request, tmp_path, quippy, monkeypatch, run_dir='run_dir'):
    print('getting fitting data from ', request.fspath)

    # kinda ugly, but remote running of multistage fit doesn't support absolute run_dir, so test
    # with a relative one
    monkeypatch.chdir(tmp_path)
    (tmp_path / run_dir).mkdir()

    GAP = fit(ConfigSet_in(input_files=os.path.join(os.path.dirname(request.fspath), 'assets', 'B_DFT_data.xyz')),
              run_dir=run_dir,
              GAP_name='GAP.B_test', params=params, ref_property_prefix='REF_',
              database_modify_mod='wfl.fit.modify_database.gap_rss_set_config_sigmas_from_convex_hull',
              calc_fitting_error=True, num_committee=3, seeds=[5, 10], committee_extra_seeds=[20, 25])
    print('GAP', GAP)

    # for debugging purposes
    print(f'ls -l {tmp_path}/{run_dir}')
    os.system(f'ls -l {tmp_path}/{run_dir}')
    print(f'cat {tmp_path}/{run_dir}/stdout.GAP.B_test.stage_1.gap_fit')
    os.system(f'cat {tmp_path}/{run_dir}/stdout.GAP.B_test.stage_1.gap_fit')

    for i in range(3):
        assert os.path.exists(os.path.join(tmp_path, run_dir, f'GAP.B_test.committee_{i}.xml'))
        assert os.path.getsize(os.path.join(tmp_path, run_dir, f'GAP.B_test.committee_{i}.xml')) > 0

    assert os.path.exists(os.path.join(tmp_path, run_dir, f'GAP.B_test.committee_{i}.xml'))
    with open(f'{tmp_path}/{run_dir}/GAP.B_test.committee_0.xml.fitting_err.json') as fin:
        fit_err = json.load(fin)
    assert set(fit_err.keys()) == set(['_ALL_'])
    assert set(fit_err['_ALL_'].keys()) == set(['energy_per_atom', 'forces', 'virial_per_atom'])


@pytest.mark.skipif(not shutil.which("gap_fit"), reason="gap_fit not in PATH")  # skips it if gap_fit not in path
@pytest.mark.remote
def test_gap_multistage_fit_remote(request, tmp_path, quippy, expyre_systems, monkeypatch):
    ri = {'resources' : {'max_time': '10m', 'n': [1, 'nodes']},
          'pre_cmds': [ f'export PYTHONPATH={Path(__file__).parent.parent}:$PYTHONPATH']}

    for sys_name in expyre_systems:
        if sys_name.startswith('_'):
            continue

        ri['sys_name'] = sys_name
        ri['job_name'] = 'pytest_gap_fit_'+sys_name

        if 'WFL_PYTEST_REMOTEINFO' in os.environ:
            ri_extra = json.loads(os.environ['WFL_PYTEST_REMOTEINFO'])
            if 'resources' in ri_extra:
                ri['resources'].update(ri_extra['resources'])
                del ri_extra['resources']
            ri.update(ri_extra)

        monkeypatch.setenv('WFL_GAP_MULTISTAGE_FIT_REMOTEINFO', json.dumps(ri))
        test_gap_multistage_fit(request, tmp_path, quippy, monkeypatch, run_dir=f'run_dir_{sys_name}')


@pytest.mark.skipif(not shutil.which("gap_fit"), reason="gap_fit not in PATH")  # skips it if gap_fit not in path
def test_gap_multistage_fit_interrupt(request, tmp_path, quippy):
    ####################################################################################################
    normal_fit_path = os.path.join(tmp_path, 'normal_fit')
    os.mkdir(normal_fit_path)
    print('getting fitting data from ', request.fspath)
    GAP = fit(ConfigSet_in(input_files=os.path.join(os.path.dirname(request.fspath), 'assets', 'B_DFT_data.xyz')),
              run_dir=normal_fit_path,
              GAP_name='GAP.B_test', params=params, ref_property_prefix='REF_',
              database_modify_mod='wfl.fit.modify_database.gap_rss_set_config_sigmas_from_convex_hull',
              calc_fitting_error=True, num_committee=3, seeds=[5, 10], committee_extra_seeds=[20, 25],
              skip_if_present=True)
    print('GAP', GAP)

    # for debugging purposes
    print(f'ls -l {normal_fit_path}')
    os.system(f'ls -l {normal_fit_path}')
    for i in range(3):
        assert os.path.exists(os.path.join(normal_fit_path, f'GAP.B_test.committee_{i}.xml'))
        assert os.path.getsize(os.path.join(normal_fit_path, f'GAP.B_test.committee_{i}.xml')) > 0

    ####################################################################################################
    interrupted_stage_path = os.path.join(tmp_path, 'interrupted_stage_fit')
    os.mkdir(interrupted_stage_path)
    print('getting fitting data from ', request.fspath)

    try:
        GAP = fit(ConfigSet_in(input_files=os.path.join(os.path.dirname(request.fspath), 'assets', 'B_DFT_data.xyz')),
                  run_dir=interrupted_stage_path,
                  GAP_name='GAP.B_test', params=params, ref_property_prefix='REF_',
                  database_modify_mod='wfl.fit.modify_database.gap_rss_set_config_sigmas_from_convex_hull',
                  calc_fitting_error=True, num_committee=3, seeds=[5, 10], committee_extra_seeds=[20, 25],
                  skip_if_present=True)
    except:
        pass

    print('AFTER STAGE INTERRUPTION')
    print(f'ls -l {interrupted_stage_path}')
    os.system(f'ls -l {interrupted_stage_path}')

    try:
        GAP = fit(ConfigSet_in(input_files=os.path.join(os.path.dirname(request.fspath), 'assets', 'B_DFT_data.xyz')),
                  run_dir=interrupted_stage_path,
                  GAP_name='GAP.B_test', params=params, ref_property_prefix='REF_',
                  database_modify_mod='wfl.fit.modify_database.gap_rss_set_config_sigmas_from_convex_hull',
                  calc_fitting_error=True, num_committee=3, seeds=[5, 10], committee_extra_seeds=[20, 25],
                  skip_if_present=True)
    except:
        pass

    print('AFTER COMMITEEE INTERRUPTION')
    print(f'ls -l {interrupted_stage_path}')
    os.system(f'ls -l {interrupted_stage_path}')

    # final run
    GAP = fit(ConfigSet_in(input_files=os.path.join(os.path.dirname(request.fspath), 'assets', 'B_DFT_data.xyz')),
              run_dir=interrupted_stage_path,
              GAP_name='GAP.B_test', params=params, ref_property_prefix='REF_',
              database_modify_mod='wfl.fit.modify_database.gap_rss_set_config_sigmas_from_convex_hull',
              calc_fitting_error=True, num_committee=3, seeds=[5, 10], committee_extra_seeds=[20, 25],
              skip_if_present=True)

    print('GAP', GAP)

    # for debugging purposes
    print('FINAL')
    print(f'ls -l {interrupted_stage_path}')
    os.system(f'ls -l {interrupted_stage_path}')
    for i in range(3):
        assert os.path.exists(os.path.join(interrupted_stage_path, f'GAP.B_test.committee_{i}.xml'))
        assert os.path.getsize(os.path.join(interrupted_stage_path, f'GAP.B_test.committee_{i}.xml')) > 0

    # compare normal to interrupted
    for i in range(3):
        fn = os.path.join(normal_fit_path, f'GAP.B_test.committee_{i}.xml')
        fi = os.path.join(interrupted_stage_path, f'GAP.B_test.committee_{i}.xml')
        with open(fn) as fin_n, open(fi) as fin_i:
            ln = fin_n.read()
            li = fin_i.read()

        # normal
        unique_label_n = cElementTree.parse(fn).getroot().find('Potential').get('label')
        unique_label_i = cElementTree.parse(fi).getroot().find('Potential').get('label')

        # strip unique labels which are fit time dependent
        ln = re.sub(unique_label_n, '', ln)
        li = re.sub(unique_label_i, '', ln)
        # strip at_file which is run dir dependent
        ln = re.sub(r'\bat_file=\S+', '', ln)
        li = re.sub(r'\bat_file=\S+', '', li)

        assert ln == li
