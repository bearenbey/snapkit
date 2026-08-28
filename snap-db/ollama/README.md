# ollama-snap

A [snap](https://snapcraft.io/) package for [Ollama](https://ollama.com), built
from the official upstream Linux release tarball, the same artefact that
`https://ollama.com/install.sh` downloads. This snap is not published or
endorsed by the upstream project.

The snap ships:

* `ollama` is the command line client, on `PATH` after install.
* `ollama.daemon` is `ollama serve` as a systemd service, started for you.

## Build

Snapcraft builds in an isolated LXD container:

```sh
sudo snap install lxd
sudo lxd init --auto
sudo usermod -aG lxd "$USER"   # log out and back in, or use: newgrp lxd

snapkit build ollama
```

`snapkit build ollama` hands the recipe to `snapcraft pack` as it stands.
Moving it onto a newer release is the other command: `snapkit update ollama`
repoints the `source:` line at the new release, rewrites its checksum, and
builds the result.

That produces `ollama_<version>_amd64.snap` (roughly 1.5 GB, since the upstream
tarball bundles the CUDA v12 and v13 runtimes).

## Install

```sh
sudo snap install --dangerous ollama_*.snap

# Not auto-connected for a locally installed snap:
sudo snap connect ollama:opengl
sudo snap connect ollama:hardware-observe
sudo snap connect ollama:removable-media   # optional, for models on other disks

sudo snap restart ollama.daemon
ollama run llama3.2
```

`opengl` is what exposes the host NVIDIA driver (`libcuda.so.1`,
`libnvidia-ml.so.1`) and `/dev/nvidia*` to the confined server;
`hardware-observe` lets Ollama enumerate the GPUs. Without them the server still
works, on CPU only.

Verify the GPU was picked up:

```sh
snap logs ollama.daemon -n 50 | grep -i 'inference compute'
```

## Configuration

Server options are set with `snap set`, and the service restarts itself:

| Key | Environment variable | Example |
| --- | --- | --- |
| `host` | `OLLAMA_HOST` | `0.0.0.0:11434` |
| `origins` | `OLLAMA_ORIGINS` | `http://localhost:*` |
| `models` | `OLLAMA_MODELS` | `/mnt/big/ollama-models` |
| `keep-alive` | `OLLAMA_KEEP_ALIVE` | `30m` |
| `context-length` | `OLLAMA_CONTEXT_LENGTH` | `8192` |
| `num-parallel` | `OLLAMA_NUM_PARALLEL` | `2` |
| `max-loaded-models` | `OLLAMA_MAX_LOADED_MODELS` | `1` |
| `max-queue` | `OLLAMA_MAX_QUEUE` | `512` |
| `flash-attention` | `OLLAMA_FLASH_ATTENTION` | `true` |
| `kv-cache-type` | `OLLAMA_KV_CACHE_TYPE` | `q8_0` |
| `no-prune` | `OLLAMA_NOPRUNE` | `true` |
| `sched-spread` | `OLLAMA_SCHED_SPREAD` | `true` |
| `gpu-overhead` | `OLLAMA_GPU_OVERHEAD` | `1073741824` |
| `debug` | `OLLAMA_DEBUG` | `true` |

```sh
sudo snap set ollama host=0.0.0.0:11434 keep-alive=30m
snap get ollama
sudo snap unset ollama keep-alive
```

Pointing `models` outside the snap needs a matching interface connection
(`removable-media` for `/media`, `/mnt` and `/run/media`).

## Paths

| What | Where |
| --- | --- |
| Models | `/var/snap/ollama/common/models` |
| Signing key | `/var/snap/ollama/common/.ollama/id_ed25519` |
| Scratch space | `/var/snap/ollama/common/tmp` |
| Client state (per user) | `~/snap/ollama/current` |

Models survive `snap refresh` and `snap remove --purge` is what deletes them.

## Confinement notes

* The client plugs `home` and `removable-media` so `ollama create -f Modelfile`
  can read files you own. Hidden directories under `$HOME` are not readable
  through the `home` interface, so keep Modelfiles in a normal directory.
* The service runs as root inside the snap sandbox with `network-bind`; by
  default it only listens on `127.0.0.1:11434`.
* There is no `classic` fallback here. If you need Ollama to see a GPU stack
  that snapd does not bridge (an out-of-tree ROCm install, for example), the
  upstream install script remains the better fit.

## Updating to a new upstream release

```sh
`snapkit update ollama`          # or `snapkit update ollama` v0.32.14
snapcraft
```

The script rewrites the pinned release URL and its SHA-256 in
`snap/snapcraft.yaml`; the snap version itself is read out of the binary at
build time via `adopt-info`.

## Other architectures

Only `amd64` is wired up. For `arm64`, change `platforms:` and point the part's
`source`/`source-checksum` at `ollama-linux-arm64.tar.zst` from the same
release. Jetson boards additionally need the `-jetpack5`/`-jetpack6` overlay
tarball unpacked over the top, which this package does not do.
