"""Native action boundaries for frozen ZMK/Nordic Ninja build closures."""

load("@west_metadata//:projects.bzl", "PROJECTS")

def _token(project):
    return "@SRC_" + project["name"].upper().replace("-", "_") + "@"

def _repo(project):
    return "@west_" + project["name"].lower().replace("-", "_") + "//:"

def _version_impl(ctx):
    arguments = ctx.actions.args()
    for variable, flag in [("ADV_BUILD_DATE", "date"), ("ADV_BUILD_BRANCH", "branch"), ("ADV_BUILD_COMMIT", "commit")]:
        value = ctx.var.get(variable)
        if not value:
            fail("Missing --define=" + variable + "=...; use scripts/build-firmware")
        arguments.add("--" + flag, value)
    arguments.add("--flavor", ctx.attr.flavor)
    arguments.add("--output", ctx.outputs.output)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [arguments],
        outputs = [ctx.outputs.output],
        mnemonic = "ZmkVersion",
    )

version = rule(
    implementation = _version_impl,
    attrs = {
        "flavor": attr.string(values = [".", "clique"]),
        "output": attr.output(mandatory = True),
        "_generator": attr.label(default = "//scripts/bazel:version", executable = True, cfg = "exec"),
    },
)

def _length(item):
    return -len(item[1])

def _step_impl(ctx):
    roots = {token: target.label.workspace_root + "/" for target, token in ctx.attr._projects.items()}
    roots["@APP@"] = roots["@SRC_ZMK@"] + "app/"
    roots["@CONFIG@"] = "config/"
    roots["@SDK@"] = ctx.file._sdk.path[:-len("sdk_version")]
    mappings = []
    build_marker = "native/" + ctx.attr.variant + "/"
    version_inputs = [ctx.file.version] if ctx.attr.mode == "configure" or ctx.attr.needs_version else []
    for file in ctx.files.srcs + version_inputs:
        logical = None
        if file == ctx.file.version:
            logical = "@CONFIG@/version.dtsi"
        elif build_marker in file.path:
            logical = "@BUILD@/" + file.path.split(build_marker)[1]
        else:
            for token, prefix in sorted(roots.items(), key = _length):
                if file.path.startswith(prefix):
                    logical = token + "/" + file.path[len(prefix):]
                    break
        if logical == None:
            fail("Unknown firmware input root: " + file.path)
        mappings.append([file.path, logical])
    spec = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(spec, json.encode({
        "mode": ctx.attr.mode,
        "variant": ctx.attr.variant,
        "command": ctx.attr.command,
        "inputs": mappings,
        "outputs": [[file.path, "@BUILD@/" + file.path.split(build_marker)[1]] for file in ctx.outputs.outs],
        "roots": roots,
        "project_paths": {_token(project): project["path"] for project in PROJECTS},
        "project_names": {project["name"]: _token(project) for project in PROJECTS},
        "tools": {
            "@PYTHON@": ctx.executable._python.path,
            "@CMAKE@": ctx.file._cmake.path,
            "@NINJA@": ctx.file._ninja.path,
            "@DTC@": ctx.executable._dtc.path,
            "@GPERF@": ctx.executable._gperf.path,
            "@PROTOC@": ctx.file._protoc.path,
        },
        "plan": json.decode(ctx.attr.plan) if ctx.attr.mode == "configure" else [],
    }))
    configure = ctx.attr.mode == "configure"
    command = ctx.attr.command
    tools = [ctx.attr._runner[DefaultInfo].files_to_run]
    direct = ctx.files.srcs + version_inputs + [spec, ctx.file._sdk]
    transitive = []
    for token, target in [("@PYTHON@", ctx.attr._python), ("@DTC@", ctx.attr._dtc), ("@GPERF@", ctx.attr._gperf)]:
        if configure or token in command or (token == "@PYTHON@" and "@PROTOC@" in command):
            tools.append(target[DefaultInfo].files_to_run)
    if configure or "@SDK@" in command:
        transitive.append(ctx.attr._sdk_files[DefaultInfo].files)
    if configure or "@CMAKE@" in command:
        direct.append(ctx.file._cmake)
        if configure or " -P " in command:
            transitive.append(ctx.attr._cmake_files[DefaultInfo].files)
    if configure or "@PROTOC@" in command:
        transitive.append(ctx.attr._protoc_files[DefaultInfo].files)
    if configure or "@NINJA@" in command:
        direct.append(ctx.file._ninja)
    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [spec.path],
        inputs = depset(direct = direct, transitive = transitive),
        tools = tools,
        outputs = ctx.outputs.outs,
        mnemonic = ctx.attr.mnemonic,
        progress_message = ctx.attr.mnemonic + " " + ctx.label.name,
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        execution_requirements = {"block-network": "1"},
    )
    return [DefaultInfo(files = depset(ctx.outputs.outs))]

_step = rule(
    implementation = _step_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "outs": attr.output_list(),
        "version": attr.label(allow_single_file = True, mandatory = True),
        "needs_version": attr.bool(),
        "variant": attr.string(mandatory = True),
        "plan": attr.string(),
        "command": attr.string(),
        "mode": attr.string(default = "edge"),
        "mnemonic": attr.string(mandatory = True),
        "_projects": attr.label_keyed_string_dict(default = {_repo(project) + "tree": _token(project) for project in PROJECTS}),
        "_python": attr.label(default = "//scripts/bazel/zephyr:zephyr_python", executable = True, cfg = "exec"),
        "_runner": attr.label(default = "//scripts/bazel/zephyr:native_runner", executable = True, cfg = "exec"),
        "_sdk": attr.label(default = "@zephyr_sdk//:sdk_version", allow_single_file = True),
        "_sdk_files": attr.label(default = "@zephyr_sdk//:root"),
        "_cmake": attr.label(default = "@cmake_linux_x86_64//:bin/cmake", allow_single_file = True),
        "_cmake_files": attr.label(default = "@cmake_linux_x86_64//:all"),
        "_ninja": attr.label(default = "@ninja_linux_x86_64//:ninja", allow_single_file = True),
        "_dtc": attr.label(default = "//scripts/bazel/zephyr:dtc", executable = True, cfg = "exec"),
        "_gperf": attr.label(default = "//scripts/bazel/zephyr:gperf", executable = True, cfg = "exec"),
        "_protoc": attr.label(default = "@protoc_linux_x86_64//:bin/protoc", allow_single_file = True),
        "_protoc_files": attr.label(default = "@protoc_linux_x86_64//:all"),
    },
)

def firmware(name, graph, variant, version):
    """Instantiate a closure captured from this exact variant's reference build."""
    build = "native/" + variant + "/"
    roots = {_token(project): _repo(project) for project in PROJECTS}
    roots.update({"@BUILD@": ":" + build, "@APP@": "@west_zmk//:app/", "@CONFIG@": "//:config/", "@SDK@": "@zephyr_sdk//:"})
    projects = [_repo(project) + "tree" for project in PROJECTS]
    _step(
        name = name + "_configure",
        mode = "configure",
        mnemonic = "ZmkConfigure",
        srcs = ["//:config"] + projects,
        outs = [build + path[len("@BUILD@/"):] for path in graph["configured"]],
        variant = variant,
        version = version,
        plan = json.encode(graph["plan"]),
    )
    for edge in graph["edges"]:
        inputs = {}
        for path in edge["inputs"]:
            token, relative = path.split("/", 1)
            if path == "@CONFIG@/version.dtsi":
                continue
            if token != "@SDK@":
                inputs[roots[token] + relative] = True

        # Python generators load sibling modules not represented in Ninja deps.
        if "@PYTHON@" in edge["command"] or "@PROTOC@" in edge["command"]:
            for project in PROJECTS:
                if project["name"] in ["zephyr", "zmk", "nanopb", "zmk-studio-messages"]:
                    inputs[_repo(project) + "tree"] = True
        mnemonic = "ZmkGenerate"
        if "COMPILER" in edge["rule"]:
            mnemonic = "ZmkCompile"
        elif "STATIC_LIBRARY" in edge["rule"]:
            mnemonic = "ZmkArchive"
        elif "EXECUTABLE_LINKER" in edge["rule"]:
            mnemonic = "ZmkLink"
        _step(
            name = variant + "_" + edge["id"],
            srcs = sorted(inputs.keys()),
            outs = [build + path[len("@BUILD@/"):] for path in edge["outputs"]],
            command = edge["command"],
            mnemonic = mnemonic,
            variant = variant,
            version = version,
            needs_version = "@CONFIG@/version.dtsi" in edge["inputs"],
        )
    native.filegroup(name = name, srcs = [":" + build + "zephyr/zmk." + ext for ext in ["elf", "hex", "uf2"]])
