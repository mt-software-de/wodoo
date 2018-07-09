#!/usr/bin/python
"""
Manages versioning of submodules.

"""
import sys
import subprocess
import module_tools
from module_tools import get_all_non_odoo_modules
from module_tools import get_module_of_file
from module_tools import get_relative_path_to_odoo_module
from module_tools import write_manifest
from module_tools import manifest2dict
from module_tools import get_manifest_path_of_module_path
from odoo_config import translate_path_relative_to_customs_root
from odoo_config import customs_dir
import odoo_config
import shutil
import datetime
import os
import re
import time
import tempfile
from git import Repo
from git import Actor
from myconfigparser import MyConfigParser
import inspect
current_file = os.path.abspath(inspect.getfile(inspect.currentframe())) # script directory

WORK_SUFFIX = "_work"
BRANCH_WORK = "{}" + WORK_SUFFIX
BRANCH_RELEASE = "{}_release"
EMPTY = "<empty>"

FORMAT_COMMIT_STEP = """|{ticket}|
Type: {type}
Modules affected by this commit: {module}
Modules affected by this ticket: {modules}

{comment}
"""

if len(sys.argv) >= 2:
    action = sys.argv[1]
else:
    action = None

def display_help():
    print "How to use:"
    print ""
    for action in actions:
        print os.path.basename(sys.argv[0]), action, '[' if parameters.get('action', '') else '', '|'.join(parameters.get(action, [])), ']' if parameters.get('action', '') else ''
        if help.get(action, ""):
            print '\t\t', help[action]
            print ""
    print ""
    print ""
    sys.exit(-1)

def check_dirty(repo, ignore_untracked=False):
    dirty = bool(repo.is_dirty())
    if not dirty and not ignore_untracked:
        dirty = bool(repo.untracked_files)

    if dirty:
        print "Please clean-up before - it is dirty here:"
        os.chdir(customs_dir())
        os.system("git status")
        sys.exit(13)

def rungitcola(customs_dir):
    os.system('git-cola >/dev/null 2>&1 &')
    time.sleep(1.0)
    os.system('/usr/bin/pkill -9 -f git-cola')
    os.system('git-cola >/dev/null 2>&1')

def userinteractive_stage():
    os.chdir(customs_dir())
    subprocess.check_call(['/usr/bin/tig', 'status'])

def userinteractive_history(path):
    os.chdir(os.path.join(customs_dir(), path))
    try:
        subprocess.check_call(['/usr/bin/git', 'log', '-p', path])
    except Exception:
        pass

def action_commit():
    """
    Makes sure, that commits are made by affected odoo module
    """
    repo = Repo(customs_dir())
    active_branch = repo.active_branch.name

    if not active_branch.endswith(BRANCH_WORK.split("_")[-1]):
        print "You are not on the working branch. (like {}).".format(BRANCH_WORK)
        sys.exit(28)

    print "Please select what to commit - DO NOT COMMIT YET!"
    userinteractive_stage()
    if not get_staged_files(repo):
        print "No files staged - aborting"
        sys.exit(42)

    verify_stage_files(repo, allow_traces=any(x == 'allow-traces' for x in sys.argv))

    staged_files = get_staged_files(repo)

    affected_modules = get_affected_modules_of_files(staged_files)

    last_msg = ""
    for module_change in affected_modules:
        module = module_change['module']
        module_path = module_change['module_path']
        manifest_path = get_manifest_path_of_module_path(module_path)

        for file in get_staged_files(repo):
            filepath_complete = os.path.join(customs_dir(), file)
            if not os.path.isfile(filepath_complete) and not os.path.islink(filepath_complete):
                repo.git.reset("HEAD", file)
            else:
                repo.git.reset(file)
        files_of_module = [x for x in get_changed_files(repo) if x.startswith(module_change['module_path'])]
        for file in files_of_module:
            repo.git.add(file)

        if len(affected_modules) == 1:
            pass
        else:
            print "Have a look again at the module files - dont touch anything"
            userinteractive_stage()
        assert sorted(get_staged_files(repo)) == sorted(files_of_module)

        while True:
            print "Module: ", module
            if manifest_path:
                print "Path:", os.path.dirname(manifest_path)
            text = raw_input("[B]ugfix, [C]hange, [F]eature in {module_name}? Press [V] to view the source code.".format(module_name=module))
            if not text:
                sys.exit(49)
            if text.lower() in 'bfc':
                type = text.upper()
                break
            if text.strip().lower() == 'v':
                userinteractive_history(module_path)

        type_str = {
            'B': 'BUGFIX',
            'C': 'CHANGE',
            'F': 'FEATURE',
        }[type]

        text = get_text_user_input("Please describe, what was done. These contents are displayed later at squash commit", last_msg) or EMPTY
        last_msg = text
        if not text:
            print "No commit message - aborting"
            sys.exit(42)

        while True:
            user = raw_input("{}\n\nUse description from above? [Y/n] ".format(text))
            if user.upper() == 'Y' or not user:
                break
            sys.exit(20)

        text = FORMAT_COMMIT_STEP.format(
            type=type_str,
            comment=text,
            ticket=active_branch.split("_")[0],
            module=module_change['module'] or '<folder>',
            modules=', '.join((x['module'] or '<folder>') for x in affected_modules),
        )
        README_PATH = os.path.join(module_path, 'README.rst')
        if os.path.isfile(README_PATH):
            format_msg = "{}@{}\n{}\n{}".format(
                type_str,
                module or '',
                datetime.datetime.now().strftime("%Y-%m-%d"),
                text,
            )
            insert_changelog(README_PATH, format_msg)
            repo.git.add(README_PATH)
        subprocess.check_call(['/usr/bin/git', 'commit', '-m', text], cwd=customs_dir())
        put_latest_commit_in_update_log(customs_dir=customs_dir(), version=os.getenv("VERSION"), module=module, module_path=module_path)

        for x in files_of_module:
            if x in staged_files:
                staged_files.remove(x)

def put_latest_commit_in_update_log(customs_dir, version, module, module_path):
    logfile = os.path.join(customs_dir, 'changelog')
    if not os.path.isfile(logfile):
        with open(logfile, 'w') as f:
            f.write("date\tversion\tported\tmodule\tcommit\n")
    commit = subprocess.check_output(['/usr/bin/git', 'log', '--oneline'], cwd=customs_dir).split("\n")[0].split(" ")[0]
    with open(logfile, 'a') as f:
        f.write("{}\t{}\t{}\t{}\t{}\n".format(
            datetime.datetime.now().strftime("%Y-%m-%d"),
            version,
            'N',
            module_path or '<root>',
            commit
        ))

def get_affected_modules_of_files(files):
    modules = []
    for file in files:
        try:
            module, module_path = get_module_of_file(os.path.join(customs_dir(), file), return_path=True)
        except Exception:
            module, module_path = "", './'
        if not filter(lambda x: x['module'] == module, modules):
            modules.append({
                'module': module,
                'module_path': translate_path_relative_to_customs_root(module_path),
            })
    modules = sorted(modules, key=lambda x: (1 if x['module'] else 99, x['module']))
    return modules

def show_open_tickets():
    repo = Repo(customs_dir())
    os.chdir(customs_dir())
    merged = subprocess.check_output(['/usr/bin/git', 'branch', '--merged', 'deploy'], cwd=customs_dir()).split("\n")
    for branch in repo.branches:
        if branch.name.endswith("_release"):
            if branch.name not in merged:
                print branch.name.replace("_release", "")

def show_current_ticket():
    repo = Repo(customs_dir())
    os.chdir(customs_dir())
    print "You are on:\t", repo.active_branch.name.replace("_work", "")

def action_switch_ticket():
    repo = Repo(customs_dir())
    check_dirty(repo, ignore_untracked=True)
    if len(sys.argv) < 3:
        print "Please provide ticket name!"
        sys.exit(-1)

    repo = Repo(customs_dir())
    branch = sys.argv[2]
    os.chdir(customs_dir())
    branch_work = BRANCH_WORK.format(branch)
    existing_branches = [x for x in repo.branches if x.name == branch_work]
    if not existing_branches:
        print "Branch not found: {}".format(branch_work)
    subprocess.check_call(['/usr/bin/git', 'checkout', '-f', branch_work], cwd=customs_dir())

def verify_stage_files(repo, allow_traces=False):

    for file in get_staged_files(repo):
        if file.endswith(".py"):
            if os.path.isfile(file):
                with open(file) as f:
                    content = f.read()
                    if 'set_trace' in content:
                        if not allow_traces:
                            print "set_trace not allowed in staged/deployed files: {}".format(file)
                            sys.exit(33)

def action_deploy_ticket():
    """
    Puts ticket branch on deploy
    """
    simple_release_ticket('deploy')

def action_stage_ticket():
    """
    Puts ticket branch on deploy
    """
    simple_release_ticket('master')

def simple_release_ticket(dest=None):
    """
    Puts ticket branch on master;
    """
    assert dest in ['master', 'deploy']
    repo = Repo(customs_dir())
    if not repo.active_branch.name.endswith("_work"):
        print "Please switch to a ticket"
    check_dirty(repo, ignore_untracked=True)
    original_branch = repo.active_branch.name
    subprocess.check_call(['/usr/bin/git', 'push', '--set-upstream', 'origin', original_branch, ], cwd=customs_dir())
    subprocess.check_call(['/usr/bin/git', 'checkout', '-f', dest], cwd=customs_dir())
    try:
        subprocess.check_call(['/usr/bin/git', 'merge', '--log=3', '-m', "merge {} to {}".format(original_branch, dest), original_branch, ], cwd=customs_dir())
    except Exception:
        print "Automatic merge failed into {} - please edit by hand".format(dest)
        print "Afterwards do:"
        print "\todoo switch-ticket {ticket}".format(ticket=original_branch.replace(WORK_SUFFIX, ''))
    else:
        while True:
            user = raw_input("Shall i push to {} now? [Y/n] ".format(dest)) or 'y'
            if not user or user.lower() in ['y', 'n']:
                break
        if user.upper() == 'Y':
            subprocess.check_call(['/usr/bin/git', 'push'], cwd=customs_dir())
        subprocess.check_call(['/usr/bin/git', 'checkout', '-f', original_branch, ], cwd=customs_dir())

def get_affected_modules_of_commits(repo, shas):
    """

    Returns dict of module name and
    {
    'module_path',
    'changed_files':,
    'changed_contents': ...
    }

    """
    result = {}
    commits = {}
    for commit in repo.iter_commits():
        if commit.hexsha not in shas:
            continue
        for file in commit.stats.files:
            try:
                module_name, module_path, manifest_path = get_module_of_file(os.path.join(customs_dir(), file), return_manifest=True)
            except Exception:
                module_name, module_path = None, None
            commits.setdefault(module_name, {
                'commits': [],
                'module_path': module_path,
            })
            if commit.hexsha not in commits[module_name]:
                commits[module_name]['commits'].append(commit)

    for module_name in commits:
        result.setdefault(module_name, {
            'module_path': commits[module_name]['module_path'],
            'changed_files': [],
            'changed_contents': [],
        })
        for commit in commits[module_name]['commits']:
            result[module_name]['changed_files'] += commit.stats.files.keys()
            changed_files = subprocess.check_output(['/usr/bin/git', 'show', commit.hexsha], cwd=customs_dir())
            result[module_name]['changed_contents'] += [changed_files]

    return result

def action_new_ticket():
    repo = Repo(customs_dir())
    check_dirty(repo, ignore_untracked=True)
    if action in ['new-ticket'] and len(sys.argv) < 3:
        print "Please provide ticket name!"
        sys.exit(-1)

    repo = Repo(customs_dir())

    branch = sys.argv[2]
    branch_work = BRANCH_WORK.format(branch)
    branch_release = BRANCH_RELEASE.format(branch)

    if [x for x in repo.branches if x.name in [branch, branch_work, branch_release]]:
        print "Branch already exists - use switch command"
        sys.exit(49)
    else:
        subprocess.check_call(['/usr/bin/git', 'checkout', '-f', 'deploy'], cwd=customs_dir())
        subprocess.check_call(['/usr/bin/git', 'checkout', '-b', branch_release], cwd=customs_dir())
        subprocess.check_call(['/usr/bin/git', 'checkout', '-b', branch_work], cwd=customs_dir())

def get_text_user_input(prompt, default_text=None):
    filename = tempfile.mktemp(suffix='.txt')
    print prompt
    if default_text:
        with open(filename, 'w') as f:
            f.write(default_text)
    subprocess.check_call(['/usr/bin/vim', filename])
    with open(filename) as f:
        content = f.read().split("\n")
        content = [x for x in content if not x.strip().startswith("#")]
        content = '\n'.join(content)
        content = content.strip()
        return content

def get_changed_files(repo):
    return [item.a_path for item in repo.index.diff(None)] + repo.untracked_files

def get_staged_files(repo):
    return [x.a_path for x in repo.index.diff("HEAD")]

def increase_module_versions():
    """
    collects the modules of staged files
    and increases the versionnumber within
    """

def insert_changelog(readme_path, changelog, new_version=None):
    """
    Inserts into readme_path the change log
    """
    with open(readme_path) as f:
        content = f.read().split("\n")

    content += ["\n"]
    if new_version:
        content += ["Version: {}".format(new_version)]
    content += [datetime.datetime.utcnow().strftime("%Y-%m-%d")]
    content += ["========================================"]
    content += [""]
    content += [changelog]

    with open(readme_path, 'w') as f:
        f.write('\n'.join(content))

def dirty(doprint=True, interactive=True):
    repo = Repo(customs_dir())
    bool(repo.is_dirty() or repo.untracked_files)

    def unstage():
        for file in get_staged_files(repo):
            repo.git.reset(file)

    unstage()
    changed_files = get_changed_files(repo)

    affected_modules = []
    for mod in sorted(get_all_non_odoo_modules(return_relative_manifest_paths=True)):
        module_path = os.path.dirname(mod)

        files = [x for x in changed_files if x.startswith(module_path)]
        if files:
            affected_modules.append(module_path)
            [changed_files.remove(x) for x in files]

    if doprint:
        if affected_modules:
            print ""
            print "Modules:"
            print "----------------------------------"
            for mod in affected_modules:
                print "module {}\t[{}]".format(get_module_of_file(mod), mod)

        if changed_files:
            print ""
            print "Other:"
            for file in changed_files:
                print file

    def get_modified_files_of_module(manifest_path):
        changed_files = get_changed_files(repo)
        module_path = os.path.dirname(manifest_path)

        module_files = [x for x in changed_files if x.startswith(module_path)]
        return module_files

    def do_interactive_commit(changed_files, manifest_path):
        for x in changed_files:
            repo.git.add(x)
        print "Changed files detected, i put them on stage; please select/deselect for stage"
        print "Just close git-cola - DO NOT COMMIT YET"
        rungitcola(customs_dir())
        files = get_staged_files(repo)
        if not files:
            user = raw_input("No files staged - try again? Otherwise abort. [y]/[N]")
            if user.lower() == 'n':
                sys.exit(0)
                return
        print ""
        print "On Stage:"
        for file in files:
            print "\t{}".format(file)

        while True:
            user = raw_input("[B]ugfix, [F]eature, [C]ancel?")
            if not user or user.lower() in ['c', 'b', 'f']:
                break

        if user.lower() == 'c':
            sys.exit(0)

        type = user.upper()

        text = get_text_user_input("Please describe, what was done. To finish type 'done' and hit return.")
        if not text:
            sys.exit(49)

        format_msg = "{}@{}\n\n{}".format(
            'BUGFIX' if type == 'B' else 'FEATURE',
            get_module_of_file(os.path.join(customs_dir(), manifest_path)) if manifest_path else "",
            text
        )

        # on module update increment the version:
        if manifest_path:
            d = manifest2dict(os.path.join(customs_dir(), manifest_path))
            module_name, module_path = get_module_of_file(os.path.join(customs_dir(), manifest_path), return_path=True)

            v = d['version']
            v = v.split('.')
            if len(v) == 2:
                v.append('0')
            if type == 'B':
                v[-1] = long(v[-1]) + 1
            elif type == 'F':
                v[-2] = long(v[-2]) + 1
            else:
                raise Exception("Not implemented: {}".format(type))
            v = '.'.join(str(x) for x in v)
            d['version'] = v
            write_manifest(os.path.join(customs_dir(), manifest_path), d)

            README_PATH = os.path.join(module_path, 'README.rst')
            if os.path.isfile(README_PATH):
                insert_changelog(README_PATH, format_msg, new_version=v)
                repo.git.add(README_PATH)

            repo.git.add(manifest_path)

        author = Actor(os.environ['USER'], os.environ['USER'])
        repo.index.commit(format_msg, author=author, committer=author)

        print "Successfully commited: \n\n".format(format_msg)

        if '/common/' in manifest_path:
            while True:
                user = input("Try to push subtree? [Y/n]") or 'y'
                if user.lower() in 'yn':
                    break
            if user == 'y':
                # TODO
                pass

        new_changed_files = [x for x in get_changed_files(repo) if x in changed_files]
        return new_changed_files

    if interactive and (changed_files or affected_modules):
        print ""
        user_commit = raw_input("Going to commit? [Y/n] ")
        if user_commit.lower() == 'n':
            sys.exit(0)

        while changed_files:
            changed_files = do_interactive_commit(changed_files, manifest_path=None)

        for module in affected_modules:
            changed_files = get_modified_files_of_module(module)
            if not changed_files:
                continue
            while changed_files:
                module_name, module_path, manifest_path = get_module_of_file(os.path.join(customs_dir(), changed_files[0]), return_manifest=True)
                manifest_path = translate_path_relative_to_customs_root(manifest_path)
                changed_files = do_interactive_commit(changed_files, manifest_path=manifest_path)

def unit_tests_git():
    r = {
    }

    def reset_repo():
        global repo

        path = tempfile.mkdtemp(suffix='')

        path_repo = os.path.join(path, 'repo1')
        print path_repo
        os.mkdir(path_repo)

        repo = Repo.init(path_repo, bare=False)
        print repo.is_dirty()
        file1 = os.path.join(path_repo, 'file1.txt')
        with open(file1, 'w') as f:
            f.write("1")
        r['repo'] = repo
        r['file1'] = file1
        r['author'] = Actor("User1", "user1@home.de")

        r['path_subrepo'] = os.path.join(path, 'subrepo')
        r['subrepo'] = Repo.init(r['path_subrepo'])
        file1 = os.path.join(r['path_subrepo'], 'subfile1.txt')
        with open(file1, 'w') as f:
            f.write("sub 1")

    def case1():
        "commit a file"
        global repo
        reset_repo()
        repo = r['repo']
        file1 = r['file1']
        assert len(repo.untracked_files) == 1
        changed_files = [item.a_path for item in repo.index.diff(None)]
        # staged_files = [item.a_path for item in repo.index.diff("HEAD")]
        repo.index.add([os.path.basename(file1)])
        repo.index.commit("msg1", author=r['author'], committer=r['author'])
        # repo.git.add(update=True) # no new files

        with open(file1, 'w') as f:
            f.write("2")
        changed_files = [item.a_path for item in repo.index.diff(None)]
        repo.index.add(changed_files)
        repo.index.commit("msg2", author=r['author'], committer=r['author'])

        file2 = os.path.join(repo.working_dir, 'file2.txt')
        with open(file2, 'w') as f:
            f.write("3")
        assert len(repo.untracked_files) == 1
        repo.index.add(repo.untracked_files)
        repo.index.commit("msg3", author=r['author'], committer=r['author'])

        os.unlink(file2)

        # caution: commit updates and deletes by directory
        # TODO extract following code
        for diff in repo.index.diff(None):
            if not diff.b_mode and not diff.b_blob:
                # deleted
                repo.index.remove(items=[diff.a_path])
            else:
                repo.index.add(items=[diff.a_path])
        repo.index.commit("deleted", author=r['author'], committer=r['author'])

    def case_empty_commit():
        global repo
        reset_repo()
        repo = r['repo']
        file1 = r['file1']
        repo.index.add([os.path.basename(file1)])
        repo.index.commit("msg1", author=r['author'], committer=r['author'])

        # now empty commit
        repo.index.commit("msg empty")

    def case_submodule():
        global repo
        reset_repo()
        repo = r['repo']
        file1 = r['file1']
        assert len(repo.untracked_files) == 1
        # changed_files = [item.a_path for item in repo.index.diff(None)]
        # staged_files = [item.a_path for item in repo.index.diff("HEAD")]
        repo.index.add([os.path.basename(file1)])
        repo.index.commit("msg1", author=r['author'], committer=r['author'])

        # clone submodule
        subrepo_dir = 'subrepo'
        repo.clone_from(r['path_subrepo'], os.path.join(repo.working_dir, subrepo_dir))

        repo.index.commit("subrepo added")

    # case1()
    # case_empty_commit()
    case_submodule()

def unit_tests():
    # make new customs
    customs = 'vunittest'

    assert '/' + customs in customs_dir(), customs_dir()

    def make_module(parent_path, modulename):
        os.chdir(parent_path)
        for v in ['7.0', '9.0']:
            subprocess.check_call('mkdir -p "{modulename}/{v}"'.format(**locals()), cwd=parent_path, shell=True)
            with open('{modulename}/{v}/__openerp__.py'.format(**locals()), 'w') as f:
                f.write("{'version': '1.0'}")

    def make_demo_customs(path):
        if os.path.exists(customs_dir()):
            shutil.rmtree(customs_dir())
        os.makedirs(customs_dir())
        os.chdir(customs_dir())
        os.system("git init .")
        os.system('mkdir -p common')
        os.system('mkdir -p modules')

        with open(os.path.join(customs_dir, '.version'), 'w') as f:
            f.write("9.0")
        with open(os.path.join(customs_dir, '.odoo.ast'), 'w') as f:
            f.write("")

        # make submodule
        path_modules1 = tempfile.mkdtemp()
        os.chdir(path_modules1)
        make_module(path_modules1, 'submodule1_1')
        make_module(path_modules1, 'submodule1_2')
        make_module(path_modules1, 'submodule1_3')
        make_module(os.path.join(customs_dir(), 'modules'), 'module1')
        subprocess.check_call("git init .", cwd=path_modules1, shell=True)
        subprocess.check_call("git add .; git commit -am .", cwd=path_modules1, shell=True)

        # clone submodules into common
        os.chdir(customs_dir())
        subprocess.check_call('git clone "{}" common/submodule1'.format(path_modules1), cwd=customs_dir(), shell=True)

    def v(*params):
        cmd = [current_file] + list(params)
        subprocess.check_call(cmd, cwd=customs_dir())

    make_demo_customs(customs_dir())
    odoo_config.set_customs(customs)
    # make demo customs

    print 'Start working on a new ticket - customers ticket number is ticket#1'
    v('new-ticket', 'ticket#1')
    v('new-ticket', 'ticket#2')


actions = {
    'unit-test': unit_tests,
    'unit-tests': unit_tests,
    'unit-tests-git': unit_tests_git,
    'help': display_help,
    'dirty': dirty,
    'new-ticket': action_new_ticket,
    'switch-ticket': action_switch_ticket,
    'stage-ticket': action_stage_ticket,
    'deploy-ticket': action_deploy_ticket,
    'open-tickets': show_open_tickets,
    'commit': action_commit,
    'current-ticket': show_current_ticket,
}

parameters = {
    'commit': ['allow-traces'],
    'new-ticket': ['<ticket-name>'],
    'switch-ticket': ['<ticket-name>'],
}

help = {
    'dirty': "displays dirty files and modules",
    'commit': "commits current changes of files; takes command; later provided as base for squash commit",
    'stage-ticket': "puts changes of ticket to master branch, where it can be tested",
    "deploy-ticket": "makes squash commit of ticket; increases version of modules; can be merged via git merge <ticketno>",
    'open-tickets': "displays unmerged tickets (not merged on deploy)",

}

if __name__ == '__main__':
    if action not in actions:
        print "Invalid verb: {}".format(action)
        display_help()
        sys.exit(-1)
    else:
        actions[action]()
