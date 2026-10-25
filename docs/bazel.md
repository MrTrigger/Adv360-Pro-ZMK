# Native keyboard firmware builds

`scripts/build-firmware` builds four declared firmware graphs. Left Studio enables `studio-rpc-usb-uart` and `CONFIG_ZMK_STUDIO=y`. Right Studio preserves the original right-side configuration and changes the version macro's flavor to `clique`.

Each graph has a configure action, generated files, individual compiler actions, static archives and final links. The configure action reconstructs the Ninja plan and rejects a stale captured graph. The final targets contain `zmk.elf`, `zmk.hex` and `zmk.uf2`. Named regular-file exports avoid copying Bazel runfiles or flattening package layouts.

## Change firmware configuration

Edit `config/`, then run the native caller. If the configured Ninja plan changes, recapture the affected variants and regenerate `firmware/*.bzl` with `scripts/bazel/zephyr/tools/capture_graph.py`. Retain `SOURCE-FILES.json` in each reference capture, listing its normalized source paths. The extractor uses it to declare every file copied with a generator directory and the files its plugin imports. Generated executables retain their permissions across actions. A graph change requires a real build and artifact checks. A source-only change that preserves the plan uses its existing graph.

## Update pinned dependencies

`config/west.yml` retains the upstream manifest. `scripts/bazel/west-sources.json` freezes every imported revision and archive hash. Resolve the full West manifest, fetch and verify its immutable archives, update that JSON, then recapture all affected variants. The module extension derives repository names and paths directly from that file.

The nanopb patch replaces its obsolete `pkg_resources` import with `importlib.resources` so the generator works with the shared setuptools pin. A separate patch removes its nested Bazel package boundary. Neither changes target C sources. Protoc 33.0 and Python protobuf 6.33.0 are pinned together for Studio generation.

Refresh `MODULE.bazel.lock` explicitly with `scripts/bzl mod deps --lockfile_mode=update`. CI uses `--lockfile_mode=error`. Commit the manifest, pins, regenerated graphs and lockfile as one dependency update after qualification.

## Validation checkpoints

During implementation, run `python3 -B -m unittest discover -s scripts/tests` for caller changes. The full firmware checkpoint is `scripts/build-firmware`, which builds both halves and flavors. The existing CI selector, manual workflow and weekly regression run remain in place. No keyboard flashing or physical key behavior is implied by a build or cache pass.

The personal cache helper accepts both provisioned curl config lines (`user = "reader-NAME:PASSWORD"`) and bare `reader-NAME:PASSWORD` or `writer-NAME:PASSWORD` values. It sends credentials only to the cache HTTPS origin. Local credential files stay outside Git.
