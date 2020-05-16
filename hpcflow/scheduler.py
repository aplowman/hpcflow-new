"""`hpcflow.scheduler.py`"""

from datetime import datetime
from subprocess import run, PIPE
from pprint import pprint

from hpcflow.config import Config as CONFIG
from hpcflow._version import __version__


class Scheduler(object):

    options = None
    output_dir = None
    error_dir = None

    def __repr__(self):
        out = ('{}('
               'options={!r}, '
               'output_dir={!r}, '
               'error_dir={!r}'
               ')').format(
            self.__class__.__name__,
            self.options,
            self.output_dir,
            self.error_dir,
        )
        return out

    def __init__(self, options=None, output_dir=None, error_dir=None):

        self.output_dir = output_dir or CONFIG.get('default_output_dir')
        self.error_dir = error_dir or CONFIG.get('default_error_dir')
        self.options = options


class SunGridEngine(Scheduler):

    _NAME = 'sge'
    SHEBANG = '#!/bin/bash --login'

    STATS_DELIM = '==============================================================\n'

    # Options that determine how to set the output/error directories:
    STDOUT_OPT = 'o'
    STDERR_OPT = 'e'
    STDOUT_OPT_FMT = '{}/'
    STDERR_OPT_FMT = '{}/'

    # Required options to ensure the job scripts work with hpcflow:
    REQ_OPT = ['cwd']
    REQ_PARAMETRISED_OPT = {}

    ALLOWED_USER_OPTS = [
        'pe',       # Parallel environment
        'l',        # Resource request
        'tc',       # Max running tasks
        'P',        # Project name (e.g. to which account jobs are accounted against)
    ]

    def __init__(self, options=None, output_dir=None, error_dir=None):

        for i in options:
            if i not in SunGridEngine.ALLOWED_USER_OPTS:
                msg = ('Option "{}" is not allowed for scheduler "{}". Allowed options '
                       'are: {}.')
                raise ValueError(
                    msg.format(i, SunGridEngine._NAME, SunGridEngine.ALLOWED_USER_OPTS))

        super().__init__(options=options, output_dir=output_dir, error_dir=error_dir)

    def get_formatted_options(self, max_num_tasks, task_step_size, user_opt=True,
                              name=None):

        opts = ['#$ -{}'.format(i) for i in SunGridEngine.REQ_OPT]
        opts.append('#$ -{} {}'.format(
            SunGridEngine.STDOUT_OPT,
            SunGridEngine.STDOUT_OPT_FMT.format(self.output_dir)),
        )
        opts.append('#$ -{} {}'.format(
            SunGridEngine.STDERR_OPT,
            SunGridEngine.STDERR_OPT_FMT.format(self.error_dir)),
        )
        opts += ['#$ -{} {}'.format(i, j)
                 for i, j in SunGridEngine.REQ_PARAMETRISED_OPT.items()]

        if name:
            opts += [f'#$ -N {name}']

        if user_opt:
            opts += ['#$ -{} {}'.format(k, v).strip()
                     for k, v in sorted(self.options.items())]

        opts += ['', '#$ -t 1-{}:{}'.format(max_num_tasks, task_step_size)]

        return opts

    def write_jobscript(self, dir_path, workflow_directory, command_group_order,
                        max_num_tasks, task_step_size, environment, archive,
                        alternate_scratch_dir, command_group_submission_id, name):
        """Write the jobscript.

        Parameters
        ----------
        archive : bool

        """

        js_ext = CONFIG.get('jobscript_ext')
        js_name = 'js_{}'.format(command_group_order)
        js_fn = js_name + js_ext
        js_path = dir_path.joinpath(js_fn)

        cmd_name = 'cmd_{}'.format(command_group_order)
        cmd_fn = cmd_name + js_ext

        submit_dir_relative = dir_path.relative_to(workflow_directory).as_posix()

        wk_dirs_path = ('${{ITER_DIR}}/working_dirs_{}{}').format(
            command_group_order, CONFIG.get('working_dirs_file_ext'))

        dt_stamp = datetime.now().strftime(r'%Y.%m.%d at %H:%M:%S')
        about_msg = ['# --- jobscript generated by `hpcflow` (version: {}) '
                     'on {} ---'.format(__version__, dt_stamp)]

        define_dirs_A = [
            'ROOT_DIR=`pwd`',
            'SUBMIT_DIR=$ROOT_DIR/{}'.format(submit_dir_relative),
            'ITER_DIR=$SUBMIT_DIR/iter_$ITER_IDX',
            'LOG_PATH=$ITER_DIR/log_{}.$SGE_TASK_ID'.format(command_group_order),
            'TASK_IDX=$((($SGE_TASK_ID - 1)/{}))'.format(task_step_size),
        ]

        write_cmd_exec = [(
            f'hpcflow write-runtime-files --directory $ROOT_DIR '
            f'--config-dir {CONFIG.get("config_dir")} '
            f'{command_group_submission_id} $TASK_IDX $ITER_IDX > $LOG_PATH 2>&1'
        )]

        define_dirs_B = [
            'INPUTS_DIR_REL=`sed -n "${{SGE_TASK_ID}}p" {}`'.format(wk_dirs_path),
            'INPUTS_DIR=$ROOT_DIR/$INPUTS_DIR_REL',
        ]

        if alternate_scratch_dir:
            alt_scratch_exc_path = '$ITER_DIR/{}_{}_$TASK_IDX{}'.format(
                CONFIG.get('alt_scratch_exc_file'),
                command_group_order,
                CONFIG.get('alt_scratch_exc_file_ext'),
            )
            define_dirs_B.append('ALT_SCRATCH_EXC=' + alt_scratch_exc_path)
            in_dir_scratch = 'INPUTS_DIR_SCRATCH={}/$INPUTS_DIR_REL'.format(
                alternate_scratch_dir)
            copy_to_alt = [
                ('rsync -avviz --exclude-from="${ALT_SCRATCH_EXC}" '
                 '$INPUTS_DIR/ $INPUTS_DIR_SCRATCH >> $LOG_PATH 2>&1'),
                '',
            ]
            move_from_alt = [
                '',
                ('rsync -avviz $INPUTS_DIR_SCRATCH/ $INPUTS_DIR --remove-source-files'
                 ' >> $LOG_PATH 2>&1'),
                '',
            ]
        else:
            in_dir_scratch = 'INPUTS_DIR_SCRATCH=$INPUTS_DIR'
            copy_to_alt = []
            move_from_alt = []

        define_dirs_B.append(in_dir_scratch)

        log_stuff = [
            r'printf "Jobscript variables:\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_IDX:\t ${ITER_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "ROOT_DIR:\t ${ROOT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "SUBMIT_DIR:\t ${SUBMIT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_DIR:\t ${ITER_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "LOG_PATH:\t ${LOG_PATH}\n" >> $LOG_PATH 2>&1',
            r'printf "SGE_TASK_ID:\t ${SGE_TASK_ID}\n" >> $LOG_PATH 2>&1',
            r'printf "TASK_IDX:\t ${TASK_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR_REL:\t ${INPUTS_DIR_REL}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR:\t ${INPUTS_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR_SCRATCH:\t ${INPUTS_DIR_SCRATCH}\n" >> $LOG_PATH 2>&1',
        ]

        if alternate_scratch_dir:
            log_stuff.append(
                r'printf "ALT_SCRATCH_EXC:\t ${ALT_SCRATCH_EXC}\n" >> $LOG_PATH 2>&1',
            )

        log_stuff.append(r'printf "\n" >> $LOG_PATH 2>&1')

        if environment:
            loads = [''] + environment + ['']
        else:
            loads = []

        set_task_args = (f'--directory $ROOT_DIR '
                         f'--config-dir {CONFIG.get("config_dir")} '
                         f'{command_group_submission_id} '
                         f'$TASK_IDX $ITER_IDX >> $LOG_PATH 2>&1')
        cmd_exec = [
            f'hpcflow set-task-start {set_task_args}',
            f'',
            f'cd $INPUTS_DIR_SCRATCH',
            f'. $SUBMIT_DIR/{cmd_fn}',
            f'',
            f'hpcflow set-task-end {set_task_args}',
        ]

        arch_lns = []
        if archive:
            arch_lns = [
                (f'hpcflow archive --directory $ROOT_DIR '
                 f'--config-dir {CONFIG.get("config_dir")} '
                 f'{command_group_submission_id} '
                 f'$TASK_IDX $ITER_IDX >> $LOG_PATH 2>&1'),
                ''
            ]

        js_lines = ([SunGridEngine.SHEBANG, ''] +
                    about_msg + [''] +
                    self.get_formatted_options(max_num_tasks, task_step_size, name=name) +
                    [''] +
                    define_dirs_A + [''] +
                    write_cmd_exec + [''] +
                    define_dirs_B + [''] +
                    log_stuff + [''] +
                    loads + [''] +
                    copy_to_alt +
                    cmd_exec +
                    move_from_alt +
                    arch_lns)

        # Write jobscript:
        with js_path.open('w') as handle:
            handle.write('\n'.join(js_lines))

        return js_path

    def write_stats_jobscript(self, dir_path, workflow_directory, command_group_order,
                              max_num_tasks, task_step_size, command_group_submission_id):

        js_ext = CONFIG.get('jobscript_ext')
        js_name = 'st_{}'.format(command_group_order)
        js_fn = js_name + js_ext
        js_path = dir_path.joinpath(js_fn)

        dt_stamp = datetime.now().strftime(r'%Y.%m.%d at %H:%M:%S')
        about_msg = ['# --- jobscript generated by `hpcflow` (version: {}) '
                     'on {} ---'.format(__version__, dt_stamp)]

        submit_dir_relative = dir_path.relative_to(workflow_directory).as_posix()

        define_dirs = [
            'ROOT_DIR=`pwd`',
            'SUBMIT_DIR=$ROOT_DIR/{}'.format(submit_dir_relative),
            'ITER_DIR=$SUBMIT_DIR/iter_$ITER_IDX',
            'LOG_PATH=$ITER_DIR/log_{}.$SGE_TASK_ID'.format(command_group_order),
            'TASK_IDX=$((($SGE_TASK_ID - 1)/{}))'.format(task_step_size),
        ]

        log_stuff = [
            r'printf "Jobscript variables:\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_IDX:\t ${ITER_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "ROOT_DIR:\t ${ROOT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "SUBMIT_DIR:\t ${SUBMIT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_DIR:\t ${ITER_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "LOG_PATH:\t ${LOG_PATH}\n" >> $LOG_PATH 2>&1',
            r'printf "SGE_TASK_ID:\t ${SGE_TASK_ID}\n" >> $LOG_PATH 2>&1',
            r'printf "TASK_IDX:\t ${TASK_IDX}\n" >> $LOG_PATH 2>&1',
        ]

        set_task_args = '-d $ROOT_DIR {} $TASK_IDX $ITER_IDX >> $LOG_PATH 2>&1'.format(
            command_group_submission_id)
        cmd_exec = ['hpcflow get-scheduler-stats {}'.format(set_task_args)]

        opt = self.get_formatted_options(max_num_tasks, task_step_size, user_opt=False)
        opt.append('#$ -l short')  # Temp (should be a profile option)

        js_lines = ([SunGridEngine.SHEBANG, ''] +
                    about_msg + [''] +
                    opt + [''] +
                    define_dirs + [''] +
                    log_stuff + [''] +
                    cmd_exec)

        # Write jobscript:
        with js_path.open('w') as handle:
            handle.write('\n'.join(js_lines))

        return js_path

    def get_scheduler_stats(self, scheduler_job_id, task_id):

        cmd = ['/opt/site/sge/bin/lx-amd64/qacct', '-j', str(scheduler_job_id)]
        proc = run(cmd, stdout=PIPE, stderr=PIPE)
        out = proc.stdout.decode().strip()
        _ = proc.stderr.decode().strip()

        info = {}
        qacct = out.split(SunGridEngine.STATS_DELIM)
        for i in qacct[1:]:
            keep = False
            for ln in i.splitlines():
                key, val = ln.strip().split(None, 1)
                val = val.strip()
                info.update({key: val})
                if key == 'taskid':
                    if val == 'undefined' or int(val) == task_id:
                        keep = True
            if keep:
                break
            else:
                info = {}

        return info


class DirectExecution(Scheduler):

    _NAME = 'direct'
    SHEBANG = '#!/bin/bash --login'

    def __init__(self, options=None, output_dir=None, error_dir=None):

        super().__init__(options=options, output_dir=output_dir, error_dir=error_dir)

    def write_jobscript(self, dir_path, workflow_directory, command_group_order,
                        max_num_tasks, task_step_size, environment, archive,
                        alternate_scratch_dir, command_group_submission_id):
        """Write the jobscript.

        Parameters
        ----------
        archive : bool

        """

        js_ext = CONFIG.get('jobscript_ext')
        js_name = 'js_{}'.format(command_group_order)
        js_fn = js_name + js_ext
        js_path = dir_path.joinpath(js_fn)

        cmd_name = 'cmd_{}'.format(command_group_order)
        cmd_fn = cmd_name + js_ext

        submit_dir_relative = dir_path.relative_to(workflow_directory).as_posix()

        wk_dirs_path = ('${{ITER_DIR}}/working_dirs_{}{}').format(
            command_group_order, CONFIG.get('working_dirs_file_ext'))

        dt_stamp = datetime.now().strftime(r'%Y.%m.%d at %H:%M:%S')
        about_msg = ['# --- jobscript generated by `hpcflow` (version: {}) '
                     'on {} ---'.format(__version__, dt_stamp)]

        define_dirs_A = [
            'SGE_TASK_ID=1',
            'ROOT_DIR=`pwd`',
            'SUBMIT_DIR=$ROOT_DIR/{}'.format(submit_dir_relative),
            'ITER_DIR=$SUBMIT_DIR/iter_$ITER_IDX',
            'LOG_PATH=$ITER_DIR/log_{}.$SGE_TASK_ID'.format(command_group_order),
            'TASK_IDX=$((($SGE_TASK_ID - 1)/{}))'.format(task_step_size),
        ]

        write_cmd_exec = [('hpcflow write-runtime-files -d $ROOT_DIR {} $TASK_IDX '
                           '$ITER_IDX > $LOG_PATH 2>&1').format(
                               command_group_submission_id)]

        define_dirs_B = [
            'INPUTS_DIR_REL=`sed -n "${{SGE_TASK_ID}}p" {}`'.format(wk_dirs_path),
            'INPUTS_DIR=$ROOT_DIR/$INPUTS_DIR_REL',
        ]

        if alternate_scratch_dir:
            alt_scratch_exc_path = '$ITER_DIR/{}_{}_$TASK_IDX{}'.format(
                CONFIG.get('alt_scratch_exc_file'),
                command_group_order,
                CONFIG.get('alt_scratch_exc_file_ext')
            )
            define_dirs_B.append('ALT_SCRATCH_EXC=' + alt_scratch_exc_path)
            in_dir_scratch = 'INPUTS_DIR_SCRATCH={}/$INPUTS_DIR_REL'.format(
                alternate_scratch_dir)
            copy_to_alt = [
                ('rsync -avviz --exclude-from="${ALT_SCRATCH_EXC}" '
                 '$INPUTS_DIR/ $INPUTS_DIR_SCRATCH >> $LOG_PATH 2>&1'),
                '',
            ]
            move_from_alt = [
                '',
                ('rsync -avviz $INPUTS_DIR_SCRATCH/ $INPUTS_DIR --remove-source-files'
                 ' >> $LOG_PATH 2>&1'),
                '',
            ]
        else:
            in_dir_scratch = 'INPUTS_DIR_SCRATCH=$INPUTS_DIR'
            copy_to_alt = []
            move_from_alt = []

        define_dirs_B.append(in_dir_scratch)

        log_stuff = [
            r'printf "Jobscript variables:\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_IDX:\t ${ITER_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "ROOT_DIR:\t ${ROOT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "SUBMIT_DIR:\t ${SUBMIT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_DIR:\t ${ITER_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "LOG_PATH:\t ${LOG_PATH}\n" >> $LOG_PATH 2>&1',
            r'printf "SGE_TASK_ID:\t ${SGE_TASK_ID}\n" >> $LOG_PATH 2>&1',
            r'printf "TASK_IDX:\t ${TASK_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR_REL:\t ${INPUTS_DIR_REL}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR:\t ${INPUTS_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "INPUTS_DIR_SCRATCH:\t ${INPUTS_DIR_SCRATCH}\n" >> $LOG_PATH 2>&1',
        ]

        if alternate_scratch_dir:
            log_stuff.append(
                r'printf "ALT_SCRATCH_EXC:\t ${ALT_SCRATCH_EXC}\n" >> $LOG_PATH 2>&1',
            )

        log_stuff.append(r'printf "\n" >> $LOG_PATH 2>&1')

        if environment:
            loads = [''] + environment + ['']
        else:
            loads = []

        set_task_args = '-d $ROOT_DIR {} $TASK_IDX $ITER_IDX >> $LOG_PATH 2>&1'.format(
            command_group_submission_id)
        cmd_exec = [
            'hpcflow set-task-start {}'.format(set_task_args),
            '',
            'cd $INPUTS_DIR_SCRATCH',
            '. $SUBMIT_DIR/{}'.format(cmd_fn),
            '',
            'hpcflow set-task-end {}'.format(set_task_args),
        ]

        arch_lns = []
        if archive:
            arch_lns = [
                ('hpcflow archive -d $ROOT_DIR {} $TASK_IDX $ITER_IDX >> '
                 '$LOG_PATH 2>&1'.format(command_group_submission_id)),
                ''
            ]

        js_lines = ([DirectExecution.SHEBANG, ''] +
                    about_msg + [''] +
                    define_dirs_A + [''] +
                    write_cmd_exec + [''] +
                    define_dirs_B + [''] +
                    log_stuff + [''] +
                    loads + [''] +
                    copy_to_alt +
                    cmd_exec +
                    move_from_alt +
                    arch_lns)

        # Write jobscript:
        with js_path.open('w') as handle:
            handle.write('\n'.join(js_lines))

        return js_path

    def write_stats_jobscript(self, dir_path, workflow_directory, command_group_order,
                              max_num_tasks, task_step_size, command_group_submission_id):

        js_ext = CONFIG.get('jobscript_ext')
        js_name = 'st_{}'.format(command_group_order)
        js_fn = js_name + js_ext
        js_path = dir_path.joinpath(js_fn)

        dt_stamp = datetime.now().strftime(r'%Y.%m.%d at %H:%M:%S')
        about_msg = ['# --- jobscript generated by `hpcflow` (version: {}) '
                     'on {} ---'.format(__version__, dt_stamp)]

        submit_dir_relative = dir_path.relative_to(workflow_directory).as_posix()

        define_dirs = [
            'SGE_TASK_ID=1',
            'ROOT_DIR=`pwd`',
            'SUBMIT_DIR=$ROOT_DIR/{}'.format(submit_dir_relative),
            'ITER_DIR=$SUBMIT_DIR/iter_$ITER_IDX',
            'LOG_PATH=$ITER_DIR/log_{}.$SGE_TASK_ID'.format(command_group_order),
            'TASK_IDX=$((($SGE_TASK_ID - 1)/{}))'.format(task_step_size),
        ]

        log_stuff = [
            r'printf "Jobscript variables:\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_IDX:\t ${ITER_IDX}\n" >> $LOG_PATH 2>&1',
            r'printf "ROOT_DIR:\t ${ROOT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "SUBMIT_DIR:\t ${SUBMIT_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "ITER_DIR:\t ${ITER_DIR}\n" >> $LOG_PATH 2>&1',
            r'printf "LOG_PATH:\t ${LOG_PATH}\n" >> $LOG_PATH 2>&1',
            r'printf "SGE_TASK_ID:\t ${SGE_TASK_ID}\n" >> $LOG_PATH 2>&1',
            r'printf "TASK_IDX:\t ${TASK_IDX}\n" >> $LOG_PATH 2>&1',
        ]

        set_task_args = '-d $ROOT_DIR {} $TASK_IDX $ITER_IDX >> $LOG_PATH 2>&1'.format(
            command_group_submission_id)
        cmd_exec = ['hpcflow get-scheduler-stats {}'.format(set_task_args)]

        js_lines = ([DirectExecution.SHEBANG, ''] +
                    about_msg + [''] +
                    define_dirs + [''] +
                    log_stuff + [''] +
                    cmd_exec)

        # Write jobscript:
        with js_path.open('w') as handle:
            handle.write('\n'.join(js_lines))

        return js_path
