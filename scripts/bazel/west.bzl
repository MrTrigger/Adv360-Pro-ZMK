"""Fetch the exact imported West sources without mutable branch resolution."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def _metadata_impl(ctx):
    ctx.file("BUILD.bazel", "exports_files([\"projects.bzl\"])\n")
    ctx.file("projects.bzl", "PROJECTS = " + ctx.attr.projects + "\n")

_metadata = repository_rule(implementation = _metadata_impl, attrs = {"projects": attr.string()})

def _impl(ctx):
    inputs = json.decode(ctx.read(Label("//scripts/bazel:west-sources.json")))
    _metadata(name = "west_metadata", projects = json.encode([{key: project[key] for key in ["name", "path"]} for project in inputs["projects"]]))
    for project in inputs["projects"]:
        archive = inputs["archives"][project["name"]]
        if archive["revision"] != project["revision"]:
            fail("West archive revision differs from the frozen project")
        http_archive(
            name = "west_" + project["name"].lower().replace("-", "_"),
            type = "tar.gz",
            urls = [archive["url"]],
            sha256 = archive["sha256"],
            strip_prefix = archive["strip_prefix"],
            build_file = "//scripts/bazel/zephyr/third_party:module.BUILD",
            # These are upstream's separate Bazel packages. This graph owns
            # each immutable archive as one source repository instead.
            patches = ["//scripts/bazel:remove-nanopb-builds.patch", "//scripts/bazel:nanopb-importlib-resources.patch"] if project["name"] == "nanopb" else [],
            patch_args = ["-p1"],
        )

west = module_extension(implementation = _impl)
