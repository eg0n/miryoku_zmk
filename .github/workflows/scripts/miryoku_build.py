#!/usr/bin/env python3
# Copyright 2021 Manna Harbour
# https://github.com/manna-harbour/miryoku
"""The steps of main.yml, for one build of its matrix.

    miryoku_build.py prepare   work out the build, fetch outboards, ZMK and modules
    miryoku_build.py build     run west, check the result, collect the firmware

The matrix entry arrives as JSON in $MATRIX and the workflow's inputs in $INPUTS.
`prepare` leaves what `build` needs in STATE_FILE.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("GITHUB_WORKSPACE", "."))
CONFIG_DIR = WORKSPACE / "miryoku_zmk" / "config"
OUTBOARDS_DIR = WORKSPACE / "miryoku_zmk" / ".github" / "workflows" / "outboards"
ARTIFACT_DIR = WORKSPACE / "artifacts"
ZMK_DIR = WORKSPACE / "zmk"
STATE_FILE = WORKSPACE / "miryoku_build.json"
DEBUG = os.environ.get("MIRYOKU_DEBUG") == "MIRYOKU_DEBUG_TRUE"

DEFAULT_ZMK = "zmkfirmware/zmk/main"
# Miryoku's own options: each becomes a -DMIRYOKU_<OPTION>_<VALUE> flag.
OPTIONS = ("alphas", "extra", "tap", "nav", "clipboard", "layers", "mapping")
NRFUTIL_URL = (
    "https://files.nordicsemi.com/artifactory/swtools/external/nrfutil/executables/x86_64-unknown-linux-gnu/nrfutil"
)

EX_DATAERR = 65
EX_UNAVAILABLE = 69


# --- What to build -----------------------------------------------------------


def value(matrix, key):
    """A matrix value, or "" when it is unset ("default" means unset)."""
    text = str(matrix.get(key) or "")
    return "" if text == "default" else text


def short_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:8]


def keyboard_names(board, shield):
    """(keyboard, base): the first shield, or the board when there is no shield, and the same
    without its _left/_right/_dongle part and board revision."""
    keyboard = shield.split()[0] if shield else board
    base = re.sub(r"_(left|right|dongle)", "", keyboard, count=1)
    return keyboard, re.sub(r"@.*", "", base)


def custom_config_flags(text):
    """custom_config is the older way to pass defines: lines of "#define NAME" or
    "#define NAME VALUE". Returns the flags and the lines that are neither."""
    flags, ignored = [], []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*#define\s+([A-Za-z_]\w*)(?:\s+([^\s;]+))?\s*", line)
        if match:
            name, define_value = match.groups()
            flags.append(f"{name}={define_value}" if define_value else name)
        elif line.strip():
            ignored.append(line)
    return flags, ignored


def dts_flags(keyboard_base, matrix):
    """The -D flags for the keymap's preprocessor, as DTS_EXTRA_CPPFLAGS takes them."""
    flags = ["MIRYOKU_KEYBOARD_" + re.sub(r"[^A-Za-z0-9]", "_", keyboard_base).upper()]
    flags += value(matrix, "dts_extra_cppflags").split()
    flags += custom_config_flags(value(matrix, "custom_config"))[0]
    flags += [f"MIRYOKU_{option}_{value(matrix, option)}".upper() for option in OPTIONS if value(matrix, option)]
    return ";".join("-D" + flag for flag in flags)


def split_kconfig(kconfig):
    """A kconfig entry may start with "label|", which names the variant it builds, as in
    "classic|CONFIG_PROSPECTOR_STATUS_SCREEN_CLASSIC=y". Returns (label, kconfig)."""
    first_line = kconfig.split("\n", 1)[0]
    if "|" in first_line:
        label, rest = kconfig.split("|", 1)
        return label, rest
    return "", kconfig


def varying_inputs(inputs):
    """The inputs that list more than one value: what tells the matrix's builds apart."""
    varying = set()
    for key, text in inputs.items():
        try:
            values = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            continue
        if isinstance(values, list) and len(values) > 1:
            varying.add(key)
    return varying


def artifact_name(matrix, varying):
    """Names the build's outputs after the shield (the board when there is none), plus the
    kconfig label when there is one. Every build uploads its own artifact, so each input that
    lists several values adds a part too; single builds keep the plain name."""
    shield = value(matrix, "shield")
    label, _ = split_kconfig(value(matrix, "kconfig"))

    parts = [shield or matrix["board"]]
    if shield and "board" in varying:
        parts.append(matrix["board"])
    if label:
        parts.append(label)
    for option in OPTIONS:
        if option in varying:
            parts.append(f"{option}_{matrix.get(option)}")
    for key, prefix in (
        ("dts_extra_cppflags", "dts"),
        ("custom_config", "custom"),
        ("kconfig", "kconfig"),
        ("branches", "branches"),
        ("modules", "modules"),
    ):
        if key in varying and not (key == "kconfig" and label):
            parts.append(f"{prefix}_{short_hash(str(matrix.get(key)))}")

    # Characters an artifact name can't hold, as in a board like nice_nano//zmk.
    return re.sub(r'[/:*?"<>|\\]', "_", "-".join(parts))


def parse_outboard(text):
    """An outboard file's outboard_<name>=<value> lines, as {name: value}."""
    settings = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*outboard_(\w+)=(.*)", line)
        if match:
            settings[match.group(1)] = " ".join(shlex.split(match.group(2)))
    return settings


def module_variable(ref):
    """The variable cmake_args can use for a module's checkout, named after its repository:
    eg0n/zmk-split-esb/main is ${MIRYOKU_MODULE_ZMK_SPLIT_ESB}."""
    _, _, repo, _ = parse_ref(ref)
    return "MIRYOKU_MODULE_" + re.sub(r"[^A-Za-z0-9]", "_", repo).upper()


def parse_ref(ref):
    """ "user/repo/branch" (the branch may hold more slashes) as (url, user, repo, branch)."""
    user, repo, branch = ref.split("/", 2)
    return f"https://github.com/{user}/{repo}.git", user, repo, branch


def failure(log):
    """Why a build log means failure, as (message, exit code), or None."""
    if "Invalid BOARD; see above." in log:
        return "Board not found. Unsupported or incorrect board.", EX_UNAVAILABLE
    if "Failed to locate keymap file!" in log:
        return "Keymap not found. Unsupported or incorrect keyboard.", EX_UNAVAILABLE
    if "Invalid SHIELD" in log:
        return "Keymap found but shield not found. Board used for shield.", EX_DATAERR
    keymaps = re.findall(r"Using keymap file: (\S+)", log)
    if any("/boards/" in keymap and not keymap.endswith("/settings_reset.keymap") for keymap in keymaps):
        return "Miryoku keyboard keymap not found. Unsupported or incorrect keyboard.", EX_UNAVAILABLE
    return None


# --- Doing it ----------------------------------------------------------------


class group:
    """A collapsible section of the job's log."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        print(f"::group::{self.name}", flush=True)

    def __exit__(self, *exc):
        print("::endgroup::", flush=True)


def run(*command, cwd=None):
    print("+ " + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, check=True)


def set_output(name, text):
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"{name}={text}\n")


def clone(url, ref, directory):
    run("git", "clone", "-b", ref, "--depth", "1", url, directory)


def fetch_outboards(names):
    """Keyboards defined outside ZMK: links each one's files into the config directory.
    Returns the ZMK branches and modules they ask for."""
    branches, modules = [], []
    for name in names:
        outboard_file = OUTBOARDS_DIR / name
        if not outboard_file.is_file():
            continue
        if DEBUG:
            shutil.copy(outboard_file, ARTIFACT_DIR)
        outboard = parse_outboard(outboard_file.read_text())
        if all(outboard.get(key) for key in ("repository", "ref", "from", "to")):
            url = outboard["repository"]
            if "https://" not in url:
                url = f"https://github.com/{url}.git"
            directory = WORKSPACE / name
            clone(url, outboard["ref"], directory)
            source = directory / outboard["from"].lstrip("/")
            link = CONFIG_DIR / outboard["to"]
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(source, link.parent))
        branches += outboard.get("branches", "").split()
        modules += outboard.get("modules", "").split()
    return branches, modules


def fetch_zmk(branches):
    """Clones the first branch, then merges the others into it."""
    base, *merges = branches or [DEFAULT_ZMK]
    url, _, _, branch = parse_ref(base)
    clone(url, branch, ZMK_DIR)
    if not merges:
        return

    actor = os.environ.get("GITHUB_ACTOR", "github-actions")
    run("git", "config", "user.name", actor, cwd=ZMK_DIR)
    run("git", "config", "user.email", f"{actor}@users.noreply.github.com", cwd=ZMK_DIR)
    run("git", "fetch", "--unshallow", cwd=ZMK_DIR)
    for merge in merges:
        url, user, repo, branch = parse_ref(merge)
        remote = f"{user}-{repo}"
        run("git", "remote", "add", remote, url, cwd=ZMK_DIR)
        run("git", "fetch", remote, branch, cwd=ZMK_DIR)
        run("git", "merge", f"{remote}/{branch}", cwd=ZMK_DIR)
        run("git", "remote", "remove", remote, cwd=ZMK_DIR)


def fetch_modules(modules):
    """Clones each module once. Returns {module ref: directory}."""
    directories = {}
    for module in modules:
        url, user, repo, branch = parse_ref(module)
        directory = WORKSPACE / "modules" / f"{user}-{repo}-{branch.replace('/', '_')}"
        if directory not in directories.values():
            clone(url, branch, directory)
            directories[module] = directory
    return directories


def prepare():
    matrix = json.loads(os.environ["MATRIX"])
    inputs = json.loads(os.environ.get("INPUTS") or "{}")
    board, shield = matrix["board"], value(matrix, "shield")
    keyboard, keyboard_base = keyboard_names(board, shield)
    name = artifact_name(matrix, varying_inputs(inputs))
    flags = dts_flags(keyboard_base, matrix)
    ARTIFACT_DIR.mkdir()

    with group("variables"):
        _, ignored = custom_config_flags(value(matrix, "custom_config"))
        if ignored:
            print(
                '::warning::custom_config takes only "#define NAME" and "#define NAME VALUE" '
                f"lines (no spaces in VALUE); ignored: {ignored}"
            )

        _, kconfig = split_kconfig(value(matrix, "kconfig"))
        if kconfig:
            kconfig_file = CONFIG_DIR / f"{keyboard}.conf"
            with open(kconfig_file, "a") as conf:
                conf.write(kconfig + "\n")
            if DEBUG:
                shutil.copy(kconfig_file, ARTIFACT_DIR)
        if DEBUG:
            (ARTIFACT_DIR / "dts_flags.txt").write_text(flags + "\n")
        print(f"keyboard: {keyboard} ({keyboard_base})\nartifact: {name}\ndts flags: {flags}")

    with group("matrix"):
        listing = "".join(f"{key}: '{text}'\n" for key, text in matrix.items())
        (ARTIFACT_DIR / f"{name}.matrix.txt").write_text(listing)
        print(listing, end="")

    with group("outboards"):
        outboards = [f"shields/{keyboard_base}", f"boards/{board}"] if shield else [f"boards/{keyboard_base}"]
        outboard_branches, outboard_modules = fetch_outboards(outboards)
    with group("zmk"):
        fetch_zmk(value(matrix, "branches").split() + outboard_branches)
    with group("modules"):
        module_dirs = fetch_modules(value(matrix, "modules").split() + outboard_modules)

    cmake_args = [f"-DSHIELD={shield}"] if shield else []
    if module_dirs:
        cmake_args.append("-DZMK_EXTRA_MODULES=" + ";".join(map(str, module_dirs.values())))
    cmake_args += [f"-DDTS_EXTRA_CPPFLAGS={flags}", f"-DZMK_CONFIG={CONFIG_DIR}"]

    # What cmake_args can refer to: the keyboard, and each module's checkout.
    variables = {"MIRYOKU_KEYBOARD": keyboard}
    variables.update({module_variable(ref): str(directory) for ref, directory in module_dirs.items()})
    print("cmake_args variables:\n" + "".join(f"  {key}={text}\n" for key, text in variables.items()), end="")

    STATE_FILE.write_text(
        json.dumps({"board": board, "variables": variables, "cmake_args": cmake_args, "artifact_name": name})
    )
    set_output("artifact_dir", ARTIFACT_DIR)
    set_output("artifact_name", name)


def west_build(board, cmake_args):
    """Runs the build, echoing its output. Returns (exit code, output)."""
    command = ["west", "build", "-b", board, "--", *cmake_args]
    print("+ " + shlex.join(command), flush=True)
    process = subprocess.Popen(
        command, cwd=ZMK_DIR / "app", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    )
    lines = []
    for line in process.stdout:
        sys.stdout.write(line)
        lines.append(line)
    sys.stdout.flush()
    return process.wait(), "".join(lines)


def collect_firmware(name):
    """Copies the firmware out of the build directory, named after the build."""
    build = ZMK_DIR / "app" / "build" / "zephyr"
    output = ARTIFACT_DIR / name
    if "CONFIG_BOARD_HAS_NRF5_BOOTLOADER=y" in (build / ".config").read_text().splitlines():
        # Nordic's nRF5 bootloader (the nRF52840 Dongle) takes a DFU package, not a UF2.
        if not shutil.which("nrfutil"):
            urllib.request.urlretrieve(NRFUTIL_URL, "/usr/local/bin/nrfutil")
            os.chmod("/usr/local/bin/nrfutil", 0o755)
            run("nrfutil", "install", "nrf5sdk-tools")
        shutil.copy(build / "zmk.hex", f"{output}.hex")
        run(
            "nrfutil",
            "nrf5sdk-tools",
            "pkg",
            "generate",
            "--hw-version",
            "52",
            "--sd-req",
            "0x00",
            "--application-version",
            "1",
            "--application",
            f"{output}.hex",
            f"{output}.zip",
        )
    else:
        # The first of these that the board produces, or all of them when debugging.
        for extension in ("uf2", "bin", "hex", "elf"):
            if (build / f"zmk.{extension}").is_file():
                shutil.copy(build / f"zmk.{extension}", f"{output}.{extension}")
                if not DEBUG:
                    break
    if DEBUG:
        shutil.copy(build / ".config", ARTIFACT_DIR)


def build():
    state = json.loads(STATE_FILE.read_text())
    # cmake_args is written as on a command line: variables, such as ${MIRYOKU_KEYBOARD} and
    # ${MIRYOKU_MODULE_<REPO>}, are expanded, and quotes group words.
    os.environ.update(state["variables"])
    extra_args = shlex.split(os.path.expandvars(os.environ.get("CMAKE_ARGS", "")))

    with group("setup"):
        run("west", "init", "-l", "app", cwd=ZMK_DIR)
        run("west", "update", cwd=ZMK_DIR)
        run("west", "zephyr-export", cwd=ZMK_DIR)

    with group("build"):
        code, log = west_build(state["board"], state["cmake_args"] + extra_args)
    problem = failure(log)
    if problem:
        print(f"::error::{problem[0]}")
        sys.exit(problem[1])
    if code:
        print(f"::error::Build failed with exit code {code}.")
        sys.exit(1)

    with group("copy"):
        collect_firmware(state["artifact_name"])


if __name__ == "__main__":
    steps = {"prepare": prepare, "build": build}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        sys.exit(__doc__)
    steps[sys.argv[1]]()
