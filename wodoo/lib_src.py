from pathlib import Path
import yaml
import shutil
import subprocess
import inquirer
import sys
from datetime import datetime
import os
import click
from .odoo_config import current_version
from .odoo_config import MANIFEST
from .tools import _is_dirty
from .odoo_config import customs_dir
from .cli import cli, pass_config
from .lib_clickhelpers import AliasedGroup
from .tools import split_hub_url
from .tools import autocleanpaper
from .tools import copy_dir_contents, rsync


@cli.group(cls=AliasedGroup)
@pass_config
def src(config):
    pass


def _turn_into_odoosh(path):
    content = MANIFEST()
    odoosh_path = Path("../odoo.sh")
    if not odoosh_path.exists():
        subprocess.check_call(
            [
                "git",
                "clone",
                "https://github.com/Odoo-Ninjas/odoo.sh.git",
                odoosh_path,
            ]
        )
        subprocess.check_call(
            [
                "gimera",
                "apply",
            ],
            cwd=odoosh_path.absolute(),
        )
    content["include"] = [
        [str(odoosh_path) + "/odoo.$VERSION", "odoo"],
        [str(odoosh_path) + "/enterprise.$VERSION", "enterprise"],
    ]
    content = yaml.safe_load((path / "gimera.yml").read_text())
    for subdir in ["odoo", "enterprise"]:
        if (path / subdir).is_dir() and not (path / subdir).is_symlink():
            shutil.rmtree(path / subdir)
        content["repos"] = [x for x in content["repos"] if x["path"] != subdir]

    (path / "gimera.yml").write_text(yaml.dump(content, default_flow_style=False))
    click.secho("Please reload now!", fg='yellow')


@src.command(name="init", help="Create a new odoo")
@click.argument("path", required=True)
@click.option("--odoosh", is_flag=True)
@pass_config
def init(config, path, odoosh):
    from .module_tools import make_customs

    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True)
    make_customs(path)

    odoosh and _turn_into_odoosh(Path(os.getcwd()))


@src.command(help="Makes odoo and enterprise code available from common code")
@pass_config
def make_odoo_sh_compatible(config):
    _turn_into_odoosh(customs_dir())


@src.command()
@pass_config
@click.option("-n", "--name", required=True)
@click.option("-p", "--parent-path", required=False)
def make_module(config, name, parent_path):
    cwd = parent_path or config.working_dir
    from .module_tools import make_module as _tools_make_module

    _tools_make_module(
        cwd,
        name,
    )


@src.command(name="update-ast")
@click.option("-f", "--filename", required=False)
def update_ast(filename):
    from .odoo_parser import update_cache

    started = datetime.now()
    click.echo("Updating ast - can take about one minute")
    update_cache(filename or None)
    click.echo(
        "Updated ast - took {} seconds".format((datetime.now() - started).seconds)
    )


@src.command("goto-inherited")
@click.option("-f", "--filepath", required=True)
@click.option("-l", "--lineno", required=True)
def goto_inherited(filepath, lineno):
    from .odoo_parser import goto_inherited_view

    lineno = int(lineno)
    filepath = customs_dir() / filepath
    lines = filepath.read_text().split("\n")
    filepath, lineno = goto_inherited_view(filepath, lineno, lines)
    if filepath:
        print(f"FILEPATH:{filepath}:{lineno}")


@src.command(name="show-addons-paths")
def show_addons_paths():
    from .odoo_config import get_odoo_addons_paths

    paths = get_odoo_addons_paths(relative=True)
    for path in paths:
        click.echo(path)


@src.command(name="make-modules", help="Puts all modules in /modules.txt")
@pass_config
def make_modules(config):
    modules = ",".join(MANIFEST()["install"])
    (config.dirs["customs"] / "modules.txt").write_text(modules)
    click.secho(f"Updated /modules.txt with: \n\n", fg="yellow")
    click.secho(modules)


@src.command()
@pass_config
def setup_venv(config):
    dir = customs_dir()
    os.chdir(dir)
    venv_dir = dir / ".venv"
    gitignore = dir / ".gitignore"
    if ".venv" not in gitignore.read_text().split("\n"):
        with gitignore.open("a") as f:
            f.write("\n.venv\n")

    subprocess.check_call(["python3", "-m", "venv", venv_dir.absolute()])

    click.secho("Please execute following commands in your shell:", bold=True)
    click.secho("source '{}'".format(venv_dir / "bin" / "activate"))
    click.secho("pip3 install cython")
    click.secho(
        "pip3 install -r https://raw.githubusercontent.com/odoo/odoo/{}/requirements.txt".format(
            current_version()
        )
    )
    requirements1 = (
        Path(__file__).parent.parent
        / "images"
        / "odoo"
        / "config"
        / str(current_version())
        / "requirements.txt"
    )
    click.secho("pip3 install -r {}".format(requirements1))
