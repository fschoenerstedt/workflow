import sys
import os
import warnings
import itertools
import subprocess
import json
import shlex
from copy import deepcopy
from pathlib import Path

import numpy as np

import ase.io
from ase.stress import voigt_6_to_full_3x3_stress

from wfl.configset import ConfigSet_in
from .utils import get_RemoteInfo

from expyre import ExPyRe
import wfl.scripts

def fit(fitting_configs, ACE_fname, ace_fit_params, ref_property_prefix='REF_',
        skip_if_present=False, run_dir='.',
        ace_fit_exec=str((Path(wfl.scripts.__file__).parent / 'ace_fit.jl').resolve()), dry_run=False,
        verbose=True, remote_info=None, wait_for_results=True):
    """Runs ace_fit on a set of fitting configs

    Parameters
    ----------
    fitting_configs: ConfigSet_in
        set of configurations to fit
    ACE_fname: str
        name of ACE potential (a .json). Overwrites any fileme given in the `params`. 
    ace_fit_params: dict
        parameters for ACE1pack. 
        Any file names (ACE, fitting configs) already present will be updated, 
        proprty keys to fit to will be prepended with `ref_property_prefix` and e0 
        set up, if needed. 
    ref_property_prefix: str, default 'REF\_'
        string prefix added to atoms.info/arrays keys (energy, forces, virial, stress)
    skip_if_present: bool, default False
        skip fitting if output is already present
    run_dir: str, default '.'
        directory to run in
    ace_fit_exec: str, default "wfl/scripts/ace_fit.jl"
        executable for ace_fit
    dry_run: bool, default False
        do a dry run, which returns the matrix size, rather than the potential name
    verbose: bool, default True
        print verbose output
    remote_info: dict or wfl.pipeline.utils.RemoteInfo, or '_IGNORE' or None
        If present and not None and not '_IGNORE', RemoteInfo or dict with kwargs for RemoteInfo
        constructor which triggers running job in separately queued job on remote machine.  If None,
        will try to use env var WFL_ACE_FIT_REMOTEINFO used (see below). '_IGNORE' is for
        internal use, to ensure that remotely running job does not itself attempt to spawn another
        remotely running job.
    wait_for_results: bool, default True
        wait for results of remotely executed job, otherwise return after starting job

    Returns
    -------
    ace_fname: Path

    Environment Variables
    ---------------------
    WFL_ACE_FIT_REMOTEINFO: JSON dict or name of file containing JSON with kwargs for RemoteInfo
        contructor to be used to run fitting in separate queued job
    ACE_FIT_JULIA_THREADS: used to set JULIA_NUM_THREADS for ace_fit.jl, which will use julia multithreading (LSQ assembly)
    ACE_FIT_BLAS_THREADS: used by ace_fit.jl for number of threads to set for BLAS multithreading in ace_fit
    """

    ace_fit_params = prepare_params(fitting_configs, ACE_fname, ace_fit_params, ref_property_prefix)
    fitting_configs = prepare_configs(fitting_configs, ref_property_prefix)

    return run_ace_fit(fitting_configs, ace_fit_params,
                skip_if_present=skip_if_present, run_dir=run_dir, ace_fit_exec=ace_fit_exec, dry_run=dry_run,
                verbose=verbose, remote_info=remote_info, wait_for_results=wait_for_results)


def prepare_params(fitting_configs, ACE_fname, ace_fit_params, ref_property_prefix='REF_'):
    """Prepare ace_fit parameters so they are compatible with the rest of workflow.
    Runs ace_fit on a a set of fitting configs

    Parameters
    ----------
    fitting_configs: ConfigSet_in
        set of configurations to fit
    ACE_fname: str
        name of ACE potential, with .json or .yace suffix 
    ace_fit_params: dict
        dict with all fitting parameters for ACE1pack, 
        to be updated with ACE_fname and ref_property_prefix 
    ref_property_prefix: str, default 'REF\_'
        string prefix added to atoms.info/arrays keys (energy, forces, virial, stress)

    Returns
    -------
    ace_fit_params: Dict
        with updated `ACE_fname`, energy/force/virial keys and e0 values. 

    """

    assert isinstance(ref_property_prefix, str) and len(ref_property_prefix) > 0

    ace_fit_params = deepcopy(ace_fit_params)

    if "ACE_fname" in ace_fit_params.keys():
        warnings.warn(f"Saving the potential to {ACE_fname}, not {ace_fit_params['ACE_fit_params']} found in ace_fit params")
    ace_fit_params["ACE_fname"] = str(ACE_fname)

    if "data" not in ace_fit_params.keys():
        ace_fit_params["data"] = {}

    ace_fit_params["data"]["energy_key"] = f"{ref_property_prefix}energy"
    ace_fit_params["data"]["force_key"] = f"{ref_property_prefix}forces"
    ace_fit_params["data"]["virial_key"] = f"{ref_property_prefix}virial" # TODO is this correct?

    _prepare_e0(ace_fit_params, fitting_configs, ref_property_prefix)

    return ace_fit_params


def prepare_configs(fitting_configs, ref_property_prefix='REF_'):
    """Prepare configs before fitting. Currently only converts stress to virial."""

    # configs need to be in memory so they can be modified with stress -> virial, and safest to
    # have them as a list (rather than using ConfigSet_in.to_memory()) when passing to ase.io.write below
    fitting_configs = list(fitting_configs)

    # calculate virial from stress, since ASE uses stress but ace_fit.jl only knows about virial
    _stress_to_virial(fitting_configs, ref_property_prefix)

    return fitting_configs

def run_ace_fit(fitting_configs, ace_fit_params, skip_if_present=False, run_dir='.', 
        ace_fit_exec=str((Path(wfl.scripts.__file__).parent / 'ace_fit.jl').resolve()), dry_run=False, 
        verbose=True, remote_info=None, wait_for_results=True):
    """Runs ace_fit on a a set of fitting configs

    Parameters
    ----------
    fitting_configs: ConfigSet_in
        set of configurations to fit
    ace_fit_params: dict
        dict with all fitting parameters for ACE1pack. 
        Only any file names (ACE, fitting configs) already present will be updated.
    skip_if_present: bool, default False
        skip fitting if output is already present
    run_dir: str, default '.'
        directory to run in
    ace_fit_exec: str, default "wfl/scripts/ace_fit.jl"
        executable for ace_fit
    dry_run: bool, default False
        do a dry run, which returns the matrix size, rather than the potential name
    verbose: bool, default True
        print verbose output
    remote_info: dict or wfl.pipeline.utils.RemoteInfo, or '_IGNORE' or None
        If present and not None and not '_IGNORE', RemoteInfo or dict with kwargs for RemoteInfo
        constructor which triggers running job in separately queued job on remote machine.  If None,
        will try to use env var WFL_ACE_FIT_REMOTEINFO used (see below). '_IGNORE' is for
        internal use, to ensure that remotely running job does not itself attempt to spawn another
        remotely running job.
    wait_for_results: bool, default True
        wait for results of remotely executed job, otherwise return after starting job

    Returns
    -------
    ace_fname: Path

    Environment Variables
    ---------------------
    WFL_ACE_FIT_REMOTEINFO: JSON dict or name of file containing JSON with kwargs for RemoteInfo
        contructor to be used to run fitting in separate queued job
    ACE_FIT_JULIA_THREADS: used to set JULIA_NUM_THREADS for ace_fit.jl, which will use julia multithreading (LSQ assembly)
    ACE_FIT_BLAS_THREADS: used by ace_fit.jl for number of threads to set for BLAS multithreading in ace_fit

    """
    ace_fit_params = deepcopy(ace_fit_params)
    ace_fit_params["ACE_fname"] = os.path.join(run_dir, ace_fit_params["ACE_fname"])
    ace_file_base = os.path.splitext(ace_fit_params["ACE_fname"])[0]

    # return early if fit calculations are done and output files are present (and readable, when that's
    # possible to check)
    if skip_if_present:
        try:
            if dry_run:
                return _read_size(ace_file_base)

            _check_output_files(ace_file_base)

            return str(ace_file_base)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            # continue below for actual size calculation or fitting
            pass

    remote_info = get_RemoteInfo(remote_info, 'WFL_ACE_FIT_REMOTEINFO')
    if remote_info is not None and remote_info != '_IGNORE':
        input_files = remote_info.input_files.copy()
        output_files = remote_info.output_files.copy()

        # put configs in memory so they can be staged out easily
        fitting_configs = fitting_configs.in_memory()

        # run dir will contain only things created by fitting, so it's safe to copy the
        # entire thing back as output
        output_files.append(str(run_dir))

        xpr = ExPyRe(name=remote_info.job_name, pre_run_commands=remote_info.pre_cmds, post_run_commands=remote_info.post_cmds,
                      env_vars=remote_info.env_vars, input_files=input_files, output_files=output_files, function=run_ace_fit,
                      kwargs= {'fitting_configs': fitting_configs, 'ace_fit_params': ace_fit_params,
                               'run_dir': str(run_dir), 'ace_fit_exec': ace_fit_exec,
                               'dry_run': dry_run, 'verbose': verbose, 'remote_info': '_IGNORE'})

        xpr.start(resources=remote_info.resources, system_name=remote_info.sys_name, header_extra=remote_info.header_extra,
                  exact_fit=remote_info.exact_fit, partial_node=remote_info.partial_node)

        if not wait_for_results:
            return None
            
        results, stdout, stderr = xpr.get_results(timeout=remote_info.timeout, check_interval=remote_info.check_interval)

        sys.stdout.write(stdout)
        sys.stderr.write(stderr)

        # no outputs to rename since everything should be in run_dir
        xpr.mark_processed()

        return results

    Path(run_dir).mkdir(exist_ok=True, parents=True)

    use_params = deepcopy(ace_fit_params)

    _write_fitting_configs(fitting_configs, use_params, ace_file_base)

    ace_fit_params_filename = ace_file_base + "_fit_params.json"
    with open(ace_fit_params_filename, "w") as f:
        f.write(json.dumps(use_params, indent=4))

    cmd = f"{ace_fit_exec} --fit-params {ace_fit_params_filename} "
    if dry_run: 
        cmd += "--dry-run "
    cmd +=  f"> {ace_file_base}.stdout 2> {ace_file_base}.stderr "

    if verbose:
        print('fitting command:\n', cmd)

    return _execute_fit_command(cmd, ace_file_base, dry_run)


def _write_fitting_configs(fitting_configs, use_params, ace_file_base):
    """
    Writes fitting configs to file and updates ace fitting parameters. 
    Configurations and filename handled by Workflow overwrite any filename
    specified in parameters. 
    """

    if "data" not in use_params.keys():
        use_params["data"] = {}

    fit_cfgs_fname_given = None 
    if "fname" in use_params["data"].keys():
        fit_cfgs_fname_given = use_params["data"]["fname"]

    fit_cfgs_fname = ace_file_base + "_fitting_database.extxyz"
    if fit_cfgs_fname_given is not None and fit_cfgs_fname_given.exists():
        warnings.warn(f"File name for configs ace_params ({fit_cfgs_fname_given}), exists; "
                      f"Fitting to configs given to ace.fit(), which are in {fit_cfgs_fname}.")

    ase.io.write(fit_cfgs_fname, fitting_configs)
    use_params["data"]["fname"] = fit_cfgs_fname

def _read_size(ace_file_base):
    with open(ace_file_base + ".size") as fin:
        info = json.loads(fin.read())
    return info["lsq_matrix_shape"]

def _check_output_files(ace_file_base):
    ace_filename = ace_file_base + ".json"
    with open(ace_filename) as fin:
        try:
            _ = json.load(fin)
        except json.JSONDecodeError:
            raise ValueError(f'Cannot parse ACE file {ace_filename}')

def _execute_fit_command(cmd, ace_file_base, dry_run):

    orig_julia_num_threads = (os.environ.get('JULIA_NUM_THREADS', None))
    if 'ACE_FIT_JULIA_THREADS' in os.environ:
        os.environ['JULIA_NUM_THREADS'] = os.environ['ACE_FIT_JULIA_THREADS']

    # this will raise an error if return status is not 0
    # we could also capture stdout and stderr here, but right now that's done by shell
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        with open(ace_file_base +'.stdout') as fin:
            for l in fin:
                print('STDOUT', l, end='')

        with open(ace_file_base +'.stderr') as fin:
            for l in fin:
                print('STDERR', l, end='')

        print(f"Failure in calling ACE fitting script {cmd} with error code:", e.returncode)
        raise e

    # repeat output and error
    with open(ace_file_base +'.stdout') as fin:
        for l in fin:
            print('STDOUT', l, end='')

    with open(ace_file_base +'.stderr') as fin:
        for l in fin:
            print('STDERR', l, end='')

    if dry_run:
        return _read_size(ace_file_base)

    # run can fail without raising an exception in subprocess.run, at least make sure that
    # ACE files exist and are readable
    _check_output_files(ace_file_base)

    if orig_julia_num_threads is not None:
        os.environ['JULIA_NUM_THREADS'] = orig_julia_num_threads
    else:
        try:
            del os.environ['JULIA_NUM_THREADS']
        except KeyError:
            pass

    return Path(ace_file_base + ".json")

def _stress_to_virial(fitting_configs, ref_property_prefix):
    for at in fitting_configs:
        if ref_property_prefix + 'stress' in at.info:
            stress = at.info[ref_property_prefix + 'stress']
            if stress.shape == (6,):
                # Voigt 6-vector
                stress = voigt_6_to_full_3x3_stress(stress)

            at.info[ref_property_prefix + 'virial'] = np.array((-stress * at.get_volume()).ravel())
            

def _prepare_e0(ace_fit_params, fitting_configs, ref_property_prefix):


    isolated_atoms = [at for at in fitting_configs if len(at) == 1]
    if len(isolated_atoms) > 0 and "e0" in ace_fit_params.keys():
        raise RuntimeError("Got e0 both in isolated atoms and in ace_fit_params")

    if len(isolated_atoms) > 0:
        e0 = {}
        for at in isolated_atoms:
            e0[str(at.symbols)] = at.info[f"{ref_property_prefix}energy"]
        ace_fit_params["e0"] = e0
    else: 
        assert "e0" in ace_fit_params.keys()

    all_elements = set(list(itertools.chain(*[list(at.symbols) for at in fitting_configs])))
    assert all_elements.issubset(set(ace_fit_params["e0"].keys()))
    
    return e0
