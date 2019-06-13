"""`hpcflow.api.py`

This module contains the application programming interface (API) to `hpcflow`,
and includes functions that are called by the command line interface (CLI; in
`hpcflow.cli.py`).

"""

from pathlib import Path
from pprint import pprint
import json

from hpcflow.models import (Workflow, Project, Submission,
                            CommandGroupSubmission)
from hpcflow.init_db import init_db
from hpcflow.profiles import parse_job_profiles
from hpcflow import CONFIG, profiles


def make_workflow(dir_path=None, profile_list=None, json_file=None,
                  json_str=None, clean=False):
    """Generate a new Workflow and add it to the local database.

    Parameters
    ----------
    dir_path : str or Path, optional
        The directory in which the Workflow will be generated. By default, this
        is the working (i.e. invoking) directory.
    profile_list : list of (str or Path), optional
        List of YAML profile file paths to use to construct the Workflow. By
        default, and if `json_file` and `json_str` and not specified, all
        YAML files in the `dir_path` directory that match the profile
        specification format in the global configuration will be parsed as
        Workflow profiles. If not None, only those profiles listed will be
        parsed as Workflow profiles.
    json_file : str or Path, optional
        Path to a JSON file that represents a Workflow. By default, set to
        `None`.
    json_str : str, optional
        JSON string that represents a Workflow. By default, set to `None`.
    clean : bool, optional
        If True, all existing hpcflow data will be removed from `dir_path`.
        Useful for debugging.

    Returns
    -------
    workflow_id : int
        The insert ID of the Workflow object in the local database.

    Notes
    -----
    Specify only one of `profile_list`, `json_file` or `json_str`.

    """

    not_nones = sum(
        [i is not None for i in [profile_list, json_file, json_str]])
    if not_nones > 1:
        msg = ('Specify only one of `profile_list`, `json_file` or `json_str`.')
        raise ValueError(msg)

    project = Project(dir_path, clean=clean)  # `clean=True` whilst developing!

    if json_str:
        workflow_dict = json.loads(json_str)

    elif json_file:
        with Path(json_file).open() as handle:
            workflow_dict = json.load(handle)

    else:
        # Get workflow from YAML profiles:
        workflow_dict = parse_job_profiles(project.dir_path, profile_list)

    Session = init_db(project.db_uri, check_exists=False)
    session = Session()

    workflow = Workflow(directory=project.dir_path, **workflow_dict)

    session.add(workflow)
    session.commit()

    workflow_id = workflow.id_
    session.close()

    return workflow_id


def submit_workflow(workflow_id, dir_path=None, task_ranges=None):
    """Submit (part of) a previously generated Workflow.

    Parameters
    ----------
    workflow_id : int
        The ID of the Workflow to submit, as in the local database.
    dir_path : str or Path, optional
        The directory in which the Workflow exists. By default, this is the
        working (i.e. invoking) directory.
    task_ranges : list of tuple of int, optional

    TODO: do validation of task_ranges here? so models.workflow.add_submission
    always receives a definite `task_ranges`? What about if the number is
    indeterminate at submission time?

    """

    project = Project(dir_path)
    Session = init_db(project.db_uri, check_exists=True)
    session = Session()

    workflow = session.query(Workflow).get(workflow_id)
    submission = workflow.add_submission(project, task_ranges)

    session.commit()

    submission_id = submission.id_
    session.close()

    return submission_id


def get_workflow_ids(dir_path=None):
    """Get the IDs of existing Workflows.

    Parameters
    ----------
    dir_path : str or Path, optional
        The directory in which the Workflows exist. By default, this is the
        working (i.e. invoking) directory.

    Returns
    -------
    workflow_ids : list of int
        List of IDs of Workflows.

    """

    project = Project(dir_path)
    Session = init_db(project.db_uri, check_exists=True)
    session = Session()

    workflow_ids = [i.id_ for i in session.query(Workflow.id_)]

    session.close()

    return workflow_ids


def clean(dir_path=None):
    """Clean the directory of all content generated by `hpcflow`."""

    project = Project(dir_path)
    project.clean()


def write_cmd(cmd_group_sub_id, task=None, dir_path=None):
    """Write the commands files for a given command group submission.

    Parameters
    ----------
    cmd_group_sub_id : int
        ID of the command group submission for which a command file is to be
        generated.
    task : int, optional
        Task ID. What is this for???
    dir_path : str or Path, optional
        The directory in which the Workflow will be generated. By default, this
        is the working (i.e. invoking) directory.

    """
    project = Project(dir_path)
    Session = init_db(project.db_uri, check_exists=True)
    session = Session()

    cg_sub = session.query(CommandGroupSubmission).get(cmd_group_sub_id)
    cg_sub.write_cmd(project)

    session.commit()
    session.close()


def archive(cmd_group_sub_id, task, dir_path=None):
    """Initiate an archive of a given task.

    Parameters
    ----------
    cmd_group_sub_id : int
        ID of the command group submission for which an archive is to be
        started.
    task : int
        The task to be archived (or rather, the task whose working directory
        will be archived).
    dir_path : str or Path, optional
        The directory in which the Workflow will be generated. By default, this
        is the working (i.e. invoking) directory.

    """

    project = Project(dir_path)
    Session = init_db(project.db_uri, check_exists=True)
    session = Session()

    cg_sub = session.query(CommandGroupSubmission).get(cmd_group_sub_id)
    cg_sub.archive(task)

    session.commit()
    session.close()
