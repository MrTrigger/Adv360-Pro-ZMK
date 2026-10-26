"""Repository rule: Zephyr SDK minimal archive + arm-zephyr-eabi toolchain in one root.

Zephyr's CMake finds the SDK through ZEPHYR_SDK_INSTALL_DIR and expects the toolchain
directory beside cmake/ and sdk_version, which is how the baseline Dockerfile lays it
out (`tar -xf toolchain... -C /opt/zephyr-sdk-<v>`). `setup.sh` is not run: it only
registers a CMake user package and optionally relocates host tools, neither of which
is needed with an explicit install dir and BCR-built dtc/gperf.
"""

def _zephyr_sdk_impl(rctx):
    rctx.download_and_extract(
        url = rctx.attr.minimal_url,
        sha256 = rctx.attr.minimal_sha256,
        stripPrefix = "zephyr-sdk-" + rctx.attr.version,
    )
    rctx.download_and_extract(
        url = rctx.attr.toolchain_url,
        sha256 = rctx.attr.toolchain_sha256,
    )
    rctx.file("BUILD.bazel", """\
package(default_visibility = ["//visibility:public"])

# The complete installation root, for actions that run Zephyr's CMake (stage 0 configure).
filegroup(name = "root", srcs = glob(["**"], exclude = ["**/*.pyc"]))

# Just the compiler driver closure, for per-translation-unit compile and link actions.
filegroup(name = "arm_zephyr_eabi", srcs = glob(["arm-zephyr-eabi/**"]))

exports_files(["sdk_version", "sdk_toolchains"])
""")

zephyr_sdk = repository_rule(
    implementation = _zephyr_sdk_impl,
    attrs = {
        "version": attr.string(mandatory = True),
        "minimal_url": attr.string(mandatory = True),
        "minimal_sha256": attr.string(mandatory = True),
        "toolchain_url": attr.string(mandatory = True),
        "toolchain_sha256": attr.string(mandatory = True),
    },
)
