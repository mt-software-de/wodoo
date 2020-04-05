import json
import click
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import os
import codecs
import shutil
import uuid
try:
    from psycopg2 import IntegrityError
except Exception:
    pass
from .tools import _extract_python_libname
from .tools import _exists_table
from .tools import _execute_sql
from .odoo_config import get_env
from .odoo_config import odoo_root
from .odoo_config import run_dir
from .odoo_config import get_conn_autoclose
from .odoo_config import current_customs
from .odoo_config import current_version
from .odoo_config import current_db
from .odoo_config import customs_dir
from .odoo_config import translate_path_into_machine_path
from .odoo_config import translate_path_relative_to_customs_root
from .odoo_config import MANIFEST_FILE
from .odoo_config import MANIFEST
from .myconfigparser import MyConfigParser
import traceback
from .odoo_parser import get_view
import fnmatch
import re
import pprint
from lxml import etree
import subprocess
try:
    import xmlrpclib
except Exception:
    import xmlrpc
    from xmlrpc import client as xmlrpclib
import inspect
import sys
import threading
import glob

try:
    current_version()
except Exception:
    LANG = 'de'
else:
    if current_version() == 7.0:
        LANG = 'de'
    else:
        LANG = os.getenv("ODOO_LANG", 'de_DE')  # todo from environment
host = "http://localhost:8069"

username = "admin"
pwd = "1"


def exe(*params):
    def login(username, password):
        socket_obj = xmlrpclib.ServerProxy('%s/xmlrpc/common' % (host))
        return socket_obj.login(current_db(), username, password)
    uid = login(username, pwd)
    socket_obj = xmlrpclib.ServerProxy('%s/xmlrpc/object' % (host))
    return socket_obj.execute(current_db(), uid, pwd, *params)


def delete_qweb(modules):

    with get_conn_autoclose() as cr:
        if modules != 'all':
            cr.execute("select name from ir_module_module where name = %s", (modules,))
        else:
            cr.execute("select name from ir_module_module; ")

        def erase_view(view_id):
            cr.execute("select id from ir_ui_view where inherit_id = %s;", (view_id, ))
            for child_view_id in [x[0] for x in cr.fetchall()]:
                erase_view(child_view_id)
            cr.execute("""
            select
                id
            from
                ir_model_data
            where
                model='ir.ui.view' and res_id =%s
            """, (view_id,))
            data_ids = [x[0] for x in cr.fetchall()]

            for data_id in data_ids:
                cr.execute("delete from ir_model_data where id = %s", (data_id,))

            sp = 'sp' + uuid.uuid4().hex
            cr.execute("savepoint {}".format(sp))
            try:
                cr.execute("""
                   delete from ir_ui_view where id = %s;
                """, [view_id])
                cr.execute("release savepoint {}".format(sp))

            except IntegrityError:
                cr.execute("rollback to savepoint {}".format(sp))

        for module in cr.fetchall():
            if not DBModules.is_module_installed(module):
                continue
            cr.execute("""
                select
                    res_id
                from
                    ir_model_data
                where
                    module=%s and model='ir.ui.view' and res_id in (select id from ir_ui_view where type='qweb');
            """, [module])
            for view_id in [x[0] for x in cr.fetchall()]:
                erase_view(view_id)

def get_all_langs():
    sql = "select distinct code from res_lang where active = true;"
    with get_conn_autoclose() as cr:
        cr.execute(sql)
        langs = [x[0] for x in cr.fetchall() if x[0]]
    return langs

def get_modules_from_install_file():
    return MANIFEST()['install']

class DBModules(object):
    def __init__(self):
        pass

    @classmethod
    def check_if_all_modules_from_install_are_installed(clazz):
        for module in get_modules_from_install_file():
            if not clazz.is_module_installed(module):
                print("Module {} not installed!".format(module))
                sys.exit(32)

    @classmethod
    def abort_upgrade(clazz):
        SQL = """
            UPDATE ir_module_module SET state = 'installed' WHERE state = 'to upgrade';
            UPDATE ir_module_module SET state = 'uninstalled' WHERE state = 'to install';
        """
        with get_conn_autoclose() as cr:
            _execute_sql(cr, SQL)

    @classmethod
    def show_install_state(clazz, raise_error):
        dangling = clazz.get_dangling_modules()
        if dangling:
            print("Displaying dangling modules:")
        for row in dangling:
            print("{}: {}".format(row[0], row[1]))

        if dangling and raise_error:
            raise Exception("Dangling modules detected - please fix installation problems and retry!")

    @classmethod
    def set_uninstallable_uninstalled(clazz):
        with get_conn_autoclose() as cr:
            _execute_sql(cr, "update ir_module_module set state = 'uninstalled' where state = 'uninstallable';")

    @classmethod
    def get_dangling_modules(clazz):
        with get_conn_autoclose() as cr:
            if not _exists_table(cr, 'ir_module_module'):
                return []

            rows = _execute_sql(
                cr,
                sql="SELECT name, state from ir_module_module where state not in ('installed', 'uninstalled', 'uninstallable');",
                fetchall=True
            )
        return rows

    @classmethod
    def get_uninstalled_modules_where_others_depend_on(clazz):
        sql = """
            select
                d.name
            from
                ir_module_module_dependency d
            inner join
                ir_module_module m
            on
                m.id = d.module_id
            inner join
                ir_module_module mprior
            on
                mprior.name = d.name
            where
                m.state in ('installed', 'to install', 'to upgrade')
            and
                mprior.state = 'uninstalled';
        """
        with get_conn_autoclose() as cr:
            cr.execute(sql)
            return [x[0] for x in cr.fetchall()]

    @classmethod
    def dangling_modules(clazz):
        with get_conn_autoclose() as cr:
            cr.execute("select count(*) from ir_module_module where state in ('to install', 'to upgrade', 'to remove');")
            return cr.fetchone()[0]

    @classmethod
    def get_all_installed_modules(clazz):
        with get_conn_autoclose() as cr:
            cr.execute("select name from ir_module_module where state not in ('uninstalled', 'uninstallable', 'to remove');")
            return [x[0] for x in cr.fetchall()]

    @classmethod
    def get_module_state(clazz, module):
        with get_conn_autoclose() as cr:
            cr.execute("select name, state from ir_module_module where name = %s", (module,))
            state = cr.fetchone()
            if not state:
                return False
            return state[1]

    @classmethod
    def is_module_listed(clazz, module):
        with get_conn_autoclose() as cr:
            if not _exists_table(cr, 'ir_module_module'):
                return False
            cr.execute("select count(*) from ir_module_module where name = %s", (module,))
            return bool(cr.fetchone()[0])

    @classmethod
    def is_module_installed(clazz, module, raise_exception_not_initialized=False):
        if not module:
            raise Exception("no module given")
        with get_conn_autoclose() as cr:
            if not _exists_table(cr, 'ir_module_module'):
                if raise_exception_not_initialized:
                    raise UserWarning("Database not initialized")
                return False
            cr.execute("select name, state from ir_module_module where name = %s", (module,))
            state = cr.fetchone()
            if not state:
                return False
            return state[1] in ['installed', 'to upgrade']

def make_customs(path):
    from .tools import abort
    import click
    if not path.exists():
        abort("Path does not exist: {}".format(path))
    elif list(path.glob("*")):
        abort("Path is not empty: {}".format(path))

    import inquirer
    from git import Repo
    from .tools import copy_dir_contents
    dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))
    src_dir = dir.parent / 'customs_template'

    def _floatify(x):
        try:
            return float(x)
        except Exception:
            return 0

    versions = sorted([x.name for x in src_dir.glob("*")], key=lambda x: _floatify(x), reverse=True)
    version = inquirer.prompt([inquirer.List('version', "", choices=versions)])['version']
    del versions

    copy_dir_contents(src_dir / version, path)

    manifest_file = path / "MANIFEST"
    manifest = eval(manifest_file.read_text())

    click.echo("Checking for odoo repo at env variable 'ODOO_REPO'")
    if os.getenv("ODOO_REPO", ""):
        odoo_path = path / 'odoo'
        repo_path = Path(os.environ['ODOO_REPO'])
        repo = Repo(repo_path)
        repo.git.checkout(str(version))
        odoo_path.mkdir()
        sha = repo.head.object.hexsha
        sha = repo.git.rev_parse(sha)
        click.echo("Copying odoo with sha to local directory [{}]".format(sha))
        copy_dir_contents(repo_path, odoo_path, exclude=['.git'])
        manifest['odoo_commit'] = sha

    manifest_file.write_text(json.dumps(manifest, indent=4))

    subprocess.call(["git", "init"], cwd=path)
    subprocess.call(["git", "add", "."], cwd=path)
    subprocess.call(["git", "commit", "-am", "init"], cwd=path)

    click.secho("Initialized - please call following now.", fg='green')
    click.secho("odoo pull --oca", fg='green')
    click.secho("odoo db reset", fg='green')
    sys.exit(0)

def make_module(parent_path, module_name):
    """
    Creates a new odoo module based on a provided template.

    """
    version = current_version()
    complete_path = Path(parent_path) / Path(module_name)
    del parent_path
    if complete_path.exists():
        raise Exception("Path already exists: {}".format(complete_path))

    shutil.copytree(str(odoo_root() / 'tools/module_template' / str(version)), complete_path)
    for root, dirs, _files in os.walk(complete_path):
        if '.git' in dirs:
            dirs.remove('.git')
        for filepath in _files:
            filepath = os.path.join(root, filepath)
            with open(filepath, 'r') as f:
                content = f.read()
            content = content.replace("__module_name__", module_name)
            with open(filepath, 'w') as f:
                f.write(content)

    # enter in install file
    m = MANIFEST()
    modules = m['install']
    modules.append(module_name)
    m['install'] = modules

def restart(quick):
    if quick:
        write_debug_instruction('quick_restart')
    else:
        write_debug_instruction('restart')

def run_test_file(path):
    if not path:
        instruction = 'last_unit_test'
    else:
        instruction = 'unit_test:{}'.format(path)
    write_debug_instruction(instruction)

def search_qweb(template_name, root_path=None):
    root_path = root_path or odoo_root()
    pattern = "*.xml"
    for path, dirs, _files in os.walk(str(root_path.resolve().absolute()), followlinks=True):
        for filename in fnmatch.filter(_files, pattern):
            if filename.name.startswith("."):
                continue
            filename = Path(path) / Path(filename)
            if "static" not in filename.parts:
                continue
            filecontent = filename.read_text()
            for idx, line in enumerate(filecontent.split("\n")):
                for apo in ['"', "'"]:
                    if "t-name={0}{1}{0}".format(apo, template_name) in line and "t-extend" not in line:
                        return filename, idx + 1

def update_module(filepath, full=False):
    module = Module(filepath)
    write_debug_instruction('update_module{}:{}'.format('_full' if full else '', module.name))

def update_view_in_db_in_debug_file(filepath, lineno):
    write_debug_instruction('update_view_in_db:{}:{}'.format(filepath, lineno))

def update_view_in_db(filepath, lineno):
    filepath = translate_path_into_machine_path(filepath)
    module = Module(filepath)
    xml = filepath.read_text().split("\n")

    line = lineno
    xmlid = ""
    while line >= 0 and not xmlid:
        if "<record " in xml[line] or "<template " in xml[line]:
            line2 = line
            while line2 < lineno:
                # with search:
                match = re.findall(r'\ id=[\"\']([^\"^\']*)[\"\']', xml[line2])
                if match:
                    xmlid = match[0]
                    break
                line2 += 1

        line -= 1

    if '.' not in xmlid:
        xmlid = module.name + '.' + xmlid

    def extract_html(parent_node):
        arch = parent_node.xpath("*")
        result = None
        if arch[0].tag == "data":
            result = arch[0]
        else:
            data = etree.Element("data")
            for el in arch:
                data.append(el)
            result = data
        if result is None:
            return ""
        result = etree.tounicode(result, pretty_print=True)
        return result

    def get_arch():
        _xml = xml
        if xml and xml[0] and 'encoding' in xml[0]:
            _xml = _xml[1:]
        doc = etree.XML("\n".join(_xml))
        for node in doc.xpath("//*[@id='{}' or @id='{}']".format(xmlid, xmlid.split('.')[-1])):
            if node.tag == 'record':
                arch = node.xpath("field[@name='arch']")
            elif node.tag == 'template':
                arch = [node]
            else:
                raise Exception("impl")

            if arch:
                html = extract_html(arch[0])
                if node.tag == 'template':
                    doc = etree.XML(html)
                    datanode = doc.xpath("/data")[0]
                    if node.get('inherit_id', False):
                        datanode.set('inherit_id', node.get('inherit_id'))
                        datanode.set('name', node.get('name', ''))
                    else:
                        datanode.set('t-name', xmlid)
                        datanode.tag = 't'
                    html = etree.tounicode(doc, pretty_print=True)

                # if not inherited from anything, then base tag must not be <data>
                doc = etree.XML(html)
                if not doc.xpath("/data/*[@position] | /*[@position]"):
                    if doc.xpath("/data"):
                        html = etree.tounicode(doc.xpath("/data/*", pretty_print=True)[0])

                print(html)
                return html

        return None

    if xmlid:
        arch = get_arch()
        if '.' in xmlid:
            module, xmlid = xmlid.split('.', 1)
        if arch:
            with get_conn_autoclose() as cr:
                cr.execute("select column_name from information_schema.columns where table_name = 'ir_ui_view'")
                columns = [x[0] for x in cr.fetchall()]
                arch_column = 'arch_db' if 'arch_db' in columns else 'arch'
                arch_fs_column = 'arch_fs' if 'arch_fs' in columns else None
                module = Module.get_by_name(module)
                print("Searching view/template for {}.{}".format(module.name, xmlid))
                cr.execute("select res_id from ir_model_data where model='ir.ui.view' and module=%s and name=%s",
                             [
                                 module.name,
                                 xmlid
                             ])
                res = cr.fetchone()
                if not res:
                    print("No view found for {}.{}".format(module.name, xmlid))
                else:
                    print('updating view of xmlid: %s.%s' % (module.name, xmlid))
                    res_id = res[0]
                    cr.execute("select type from ir_ui_view where id=%s", (res_id,))
                    # view_type = cr.fetchone()[0]
                    cr.execute("update ir_ui_view set {}=%s where id=%s".format(arch_column), [
                        arch,
                        res_id
                    ])
                    cr.connection.commit()
                    if arch_fs_column:
                        try:
                            rel_path = module.name + "/" + str(filepath.relative_to(module.path))
                            cr.execute("update ir_ui_view set arch_fs=%s where id=%s", [
                                rel_path,
                                res_id
                            ])
                            cr.connection.commit()
                        except Exception:
                            cr.connection.rollback()

                    if res:
                        exe("ir.ui.view", "write", [res_id], {'arch_db': arch})


class Modules(object):

    def __init__(self):
        modnames = set()
        from .odoo_config import get_odoo_addons_paths

        def get_all_manifests():
            """
            Returns a list of full paths of all manifests
            """
            for path in get_odoo_addons_paths():
                for file in path.glob("**/" + MANIFEST_FILE()):
                    modname = file.parent.name
                    if modname in modnames:
                        continue
                    modnames.add(file.absolute())
                    yield file.absolute()

        self.modules = {}
        for m in get_all_manifests():
            self.modules[m.parent.name] = Module(m)

    def get_customs_modules(self, mode=None):
        """
        Called by odoo update

        - fetches to be installed modules from install-file
        - selects all installed, to_be_installed, to_upgrade modules from db and checks wether
          they are from "us" or OCA
          (often those modules are forgotten to be updated)

        """
        assert mode in [None, 'to_update', 'to_install']

        modules = get_modules_from_install_file()

        if mode == 'to_install':
            modules = [x for x in modules if not DBModules.is_module_installed(x)]

        return modules

    def get_module_dependency_tree(self, module):
        """
        Dict of dicts

        'stock_prod_lot_unique': {
            'stock': {
                'base':
            },
            'product': {},
        }
        """
        result = {}

        def append_deps(mod, data):
            data[mod.name] = {}
            for dep in mod.manifest_dict['depends']:
                if dep == 'base':
                    continue
                dep_mod = [x for x in self.modules.values() if x.name == dep][0]
                data[mod.name][dep] = {}
                append_deps(dep_mod, data[mod.name][dep])

        append_deps(module, result)
        return result

    def get_module_flat_dependency_tree(self, module):
        deptree = self.get_module_dependency_tree(module)
        result = set()

        def x(d):
            for k, v in d.items():
                if isinstance(k, str):
                    result.add(k)
                else:
                    result.add(k.name)
                x(v)

        x(deptree)
        assert all(isinstance(x, str) for x in result)
        return sorted(list(result))

    def get_all_used_modules(self):
        """
        Returns all modules that are directly or indirectly (auto install, depends) installed.
        """
        result = set()
        modules = self.get_customs_modules()

        auto_install_modules = []
        for module in modules:
            module = Module.get_by_name(module)
            result.add(module.name)
            if module.manifest_dict.get('auto_install', False):
                auto_install_modules.append(module)
            dependencies = self.get_module_flat_dependency_tree(module)
            for dep in dependencies:
                result.add(dep)

        # check for auto install modules - auto install could refer to other auto install
        while True:
            changed = False
            for module in list(auto_install_modules):
                depends = [x for x in module.manifest_dict.get('depends', []) if x != 'base']
                if all(x in modules for x in depends) and module.name not in result:
                    changed = True
                    result.append(module.name)
                    auto_install_modules.remove(module)
            if not changed:
                break

        return list(result)

    def get_all_external_dependencies(self):
        modules = self.get_all_used_modules()
        pydeps = []
        deb_deps = []
        for module in modules:
            module = self.modules[module]
            file = (module.path / 'external_dependencies.txt')
            if file.exists():
                try:
                    content = json.loads(file.read_text())
                except Exception as e:
                    click.secho("Error parsing json in\n{}:\n{}".format(file, e), fg='red')
                    click.secho(file.read_text(), fg='red')
                    sys.exit(1)
                pydeps += content.get("pip", [])
                deb_deps += content.get('deb', [])
            else:
                pydeps += module.manifest_dict.get('external_dependencies', {}).get('python', [])

        pydeps = list(set(pydeps))
        # check for conflicts
        for pydep in pydeps:
            x = _extract_python_libname(pydep)
            others = [y for y in pydeps if y != pydep and _extract_python_libname(y) == x]
            if others:
                # TODO evaluate >= instructions...not seen by now
                raise Exception("Not unique dependency: {}".format(
                    '\n'.join([pydep] + others)
                ))
                sys.exit(-1)

        return {
            'pip': pydeps,
            'deb': deb_deps
        }


class Module(object):

    class IsNot(Exception): pass

    def __init__(self, path):
        self.version = float(current_version())
        path = Path(path)
        p = path if path.is_dir() else path.parent

        for p in [p] + list(p.parents):
            if (p / MANIFEST_FILE()).exists():
                self._manifest_path = p / MANIFEST_FILE()
                break
        if not getattr(self, '_manifest_path', ''):
            raise Module.IsNot("no module found for {}".format(path))
        self.name = self._manifest_path.parent.name
        self.path = self._manifest_path.parent

    @property
    def manifest_path(self):
        return self._manifest_path

    @property
    def manifest_dict(self):
        try:
            content = self.manifest_path.read_text()
            content = '\n'.join(filter(lambda x: not x.strip().startswith("#"), content.split("\n")))
            return eval(content) # TODO safe
        except SyntaxError:
            click.secho("error at file: %s" % self.manifest_path, fg='red')
            raise
        except Exception:
            click.secho("error at file: %s" % self.manifest_path, fg='red')
            raise

    def __make_path_relative(self, path):
        path = path.resolve().absolute()
        path = path.relative_to(self.path)
        if not path:
            raise Exception("not part of module")
        return self.path.name / path

    def apply_po_file(self, pofile_path):
        """
        pofile_path - pathin in the machine
        """
        pofile_path = self.__make_path_relative(pofile_path)
        LANG = pofile_path.name.split(".po")[0]
        write_debug_instruction('import_i18n:{}:{}'.format(LANG, pofile_path))

    def export_lang(self, current_file, LANG):
        write_debug_instruction('export_i18n:{}:{}'.format(LANG, self.name))

    @classmethod
    def get_by_name(clazz, name):
        from .odoo_config import get_odoo_addons_paths
        path = None
        for addon_path in get_odoo_addons_paths():
            dir = addon_path / name
            if dir.exists():
                path = dir
            del dir
        if not path:
            raise Exception("Could not get path for {}".format(name))
        if path.exists():
            path = path.resolve()

        if path.is_dir():
            return Module(path)
        # could be an odoo module then
        for path in get_odoo_addons_paths():
            print(path)
            if (path / name).resolve().is_dir():
                return Module(path / name)
        raise Exception("Module not found or not linked: {}".format(name))

    @property
    def dependent_modules(self):
        """
        per modulename all dependencies - no hierarchy
        """
        result = {}
        for dep in self.manifest_dict.get('depends', []):
            result.add(Module.get_by_name(dep))

        return result

    def get_lang_file(self, lang):
        lang_file = (self.path / "i18n" / lang).with_suffix('.po')
        if lang_file.exists():
            return lang_file

    @property
    def in_version(self):
        if self.version >= 10.0:
            try:
                version = self.manifest_dict.get('version', "")
            except SyntaxError:
                return False
            # enterprise modules from odoo have versions: "", "1.0" and so on... ok
            if not version:
                return True
            if len(version.split(".")) <= 3:
                # allow 1.0 2.2 etc.
                return True
            check = str(self.version).split('.')[0] + '.'
            return version.startswith(check)
        else:
            info_file = self.path / '.ln'
            if info_file.exists():
                info = eval(info_file.read_text())
                if isinstance(info, (float, int)):
                    min_ver = info
                    max_ver = info
                    info = {'minimum_version': min_ver, 'maximum_version': max_ver}
                else:
                    min_ver = info.get("minimum_version", 1.0)
                    max_ver = info.get("maximum_version", 1000.0)
                if min_ver > max_ver:
                    raise Exception("Invalid version: {}".format(self.path))
                if self.version >= float(min_ver) and self.version <= float(max_ver):
                    return True

            elif "OCA" in self.path.parts:
                relpath = str(self.path).split(u"/OCA/")[1].split("/")
                return len(relpath) == 2
        return False

    def update_assets_file(self):
        """
        Put somewhere in the file: assets: <xmlid>, then
        asset is put there.
        """
        assets_template = """
    <odoo><data>
    <template id="{id}" inherit_id="{inherit_id}">
        <xpath expr="." position="inside">
        </xpath>
    </template>
    </data>
    </odoo>
    """
        DEFAULT_ASSETS = "web.assets_backend"

        def default_dict():
            return {
                'stylesheets': [],
                'js': [],
            }

        files_per_assets = {
            # 'web.assets_backend': default_dict(),
            # 'web.report_assets_common': default_dict(),
            # 'web.assets_frontend': default_dict(),
        }
        # try to keep assets id
        filepath = self.path / 'views/assets.xml'
        current_id = None
        if filepath.exists():
            with filepath.open('r') as f:
                xml = f.read()
                doc = etree.XML(xml)
                for t in doc.xpath("//template/@inherit_id"):
                    current_id = t

        all_files = self.get_all_files_of_module()
        if current_version() < 11.0:
            module_path = Path(str(self.path).replace("/{}/".format(current_version()), ""))
            if str(module_path).endswith("/{}".format(current_version())):
                module_path = "/".join(str(module_path).split("/")[:-1])

        for file in all_files:
            if file.name.startswith('.'):
                continue

            local_file_path = Path("/") / Path(self.path.name) / file.relative_to(self.path)

            if current_id:
                parent = current_id
            elif 'static' in local_file_path.parts:
                parent = DEFAULT_ASSETS
            elif 'report' in local_file_path.parts or 'reports' in local_file_path.parts:
                parent = 'web.report_assets_common'
            else:
                continue
            files_per_assets.setdefault(parent, default_dict())

            if file.suffix in ['.less', '.css']:
                files_per_assets[parent]['stylesheets'].append(local_file_path)
            elif file.suffix in ['.js']:
                files_per_assets[parent]['js'].append(local_file_path)

        doc = etree.XML(assets_template)
        for asset_inherit_id, _files in files_per_assets.items():
            parent = deepcopy(doc.xpath("//template")[0])
            parent.set('inherit_id', asset_inherit_id)
            parent.set('id', asset_inherit_id.split('.')[-1])
            parent_xpath = parent.xpath("xpath")[0]
            for style in _files['stylesheets']:
                etree.SubElement(parent_xpath, 'link', {
                    'rel': 'stylesheet',
                    'href': str(style),
                })
            for js in _files['js']:
                etree.SubElement(parent_xpath, 'script', {
                    'type': 'text/javascript',
                    'src': str(js),
                })
            doc.xpath("/odoo/data")[0].append(parent)

        # remove empty assets and the first template template
        for to_remove in doc.xpath("//template[1] | //template[xpath[not(*)]]"):
            to_remove.getparent().remove(to_remove)

        if not doc.xpath("//link| //script"):
            if filepath.exists():
                filepath.unlink()
        else:
            filepath.parent.mkdir(exist_ok=True)
            with filepath.open('wb') as f:
                f.write(etree.tostring(doc, pretty_print=True))

    def get_all_files_of_module(self):
        for file in self.path.glob("**/*"):
            if file.name.startswith("."):
                continue
            if ".git" in file.parts:
                continue
            # relative to module path
            yield file

    def update_module_file(self):
        # updates __openerp__.py the update-section to point to all xml files in the module;
        # except if there is a directory test; those files are ignored;
        self.update_assets_file()
        mod = self.manifest_dict

        all_files = self.get_all_files_of_module()
        # first collect all xml files and ignore test and static
        DATA_NAME = 'data'
        if current_version() <= 7.0:
            DATA_NAME = 'update_xml'

        mod[DATA_NAME] = []
        mod["qweb"] = []
        mod["js"] = []
        mod["demo_xml"] = []
        mod["css"] = []

        for f in all_files:
            local_path = str(f.relative_to(self.path))
            if 'test' in f.parts:
                continue
            if f.suffix in ['.xml', '.csv', '.yml']:
                if f.name.startswith("demo%s" % os.sep):
                    mod["demo_xml"].append(local_path)
                elif 'static' in f.parts:
                    mod["qweb"].append(local_path)
                else:
                    mod[DATA_NAME].append(local_path)
            elif f.suffix == '.js':
                mod["js"].append(local_path)
            elif f.suffix in ['.css', '.less']:
                mod["css"].append(local_path)

        # keep test empty: use concrete call to test-file instead of testing on every module update
        mod["test"] = []

        # sort
        mod[DATA_NAME].sort()
        mod["js"].sort()
        mod["css"].sort()
        if 'depends' in mod:
            mod["depends"].sort()

        # now sort again by inspecting file content - if __openerp__.sequence NUMBER is found, then
        # set this index; reason: some times there are wizards that reference forms and vice versa
        # but cannot find action ids
        # 06.05.2014: put the ir.model.acces.csv always at the end, because it references others, but security/groups always in front
        sorted_by_index = [] # contains tuples (index, filename)
        for filename in mod[DATA_NAME]:
            filename_xml = filename
            filename = self.path / filename
            sequence = 0
            with filename.open('r') as f:
                content = f.read()
                if '__openerp__.sequence' in content:
                    sequence = int(re.search(r'__openerp__.sequence[^\d]*(\d*)', content).group(1))
                elif 'odoo.sequence' in content:
                    sequence = int(re.search(r'odoo.sequence[^\d]*(\d*)', content).group(1))
                elif filename.name == 'menu.xml':
                    sequence = 1000
                elif filename.name == 'groups.xml':
                    sequence = -999999
                elif filename.name == 'ir.model.access.csv':
                    sequence = 999999
            sorted_by_index.append((sequence, filename_xml))

        sorted_by_index = sorted(sorted_by_index, key=lambda x: x[0])
        mod[DATA_NAME] = [x[1] for x in sorted_by_index]

        if mod["qweb"]:
            mod["web"] = True
        if "application" not in mod:
            mod["application"] = False

        self.write_manifest(mod)

    def write_manifest(self, data):
        with self.manifest_path.open('w') as file:
            pp = pprint.PrettyPrinter(indent=4, stream=file)
            pp.pprint(data)

def write_debug_instruction(instruction):
    from . import files
    print(files['run/odoo_debug.txt'])
    files['run/odoo_debug.txt'].write_text(instruction)
