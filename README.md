# Gource Visulization of Obsidian Vault backed by Jujutso

This project helps to convert a Jujutso tracked Obsidian Vault into
the Gource custom log format. The unique thing about this is, that
the files visualized in Gource are not under their filesystem path,
but are derived from their tags inside Obsidian, which can lead to
quite interesting patterns.

## Requirements

You need to have the following installed:
1. `jj` executable for interaction with Jujutso repo
2. `python3`

## Installation

In order to setup the required python virtual env, execute in your shell:
```bash
./make_venv.sh
```

Afterwards you can make a symlink from `obsidian-gource-vis-jj` to your `/usr/local/bin` folder (or wherever you store your $PATH executables).
*OPTIONAL*

## Usage

Run in your terminal:
```bash
obsidian-gource-vis-jj <PATH_TO_OBSIDIAN_VAULT_FOLDER>
```

This will write the gource custom log format to stdout. This can be piped directly into gource like so:
```sh
obsidian-gource-vis-jj <PATH> | gource --realtime --log-format custom -
```
Or you can write it to file and have gource read it separately.



