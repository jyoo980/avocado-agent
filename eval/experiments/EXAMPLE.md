# Example: full Avocado on `lz4_lib` with Opus 5.5

A worked example of `eval/experiments/run.sh` driving an `avocado-verify` treatment. The paths
assume the layout on this machine:

| What                     | Where                                                   |
|--------------------------|---------------------------------------------------------|
| these scripts            | `/home/jmsy/claudebox/avocado-agent`                    |
| full-avocado checkout    | `/home/jmsy/claudebox/opus-5-5/full-avocado`            |
| subject programs         | `/home/jmsy/claudebox/subject-programs/<name>`          |
| auth profiles            | `/home/jmsy/avocado-profiles/*.env`                     |
| results                  | `~/.local/share/avocado-experiments/<tag>/<timestamp>/` |

## 1. Pick the model

The full-avocado checkout's `.claude/settings.json` names `claude-opus-4-8`. A profile's
`AVOCADO_MODEL` overrides that, so instead of editing the tracked settings file, make a profile
for the model you want. The profile name becomes part of the batch tag, which keeps results for
different models apart.

```sh
cp /home/jmsy/avocado-profiles/subscription.env /home/jmsy/avocado-profiles/opus-5-5.env
printf 'AVOCADO_MODEL=claude-opus-5-5\n' >> /home/jmsy/avocado-profiles/opus-5-5.env
```

## 2. Make sure the image exists

Every treatment's `Dockerfile` is identical, so the one `avocado-agent-container` image serves
all of them. Rebuild only if it is missing:

```sh
cd /home/jmsy/claudebox/avocado-agent
make build-image
```

## 3. Launch the batch

Run from the `avocado-agent` checkout and point `--harness` at the treatment. The runner is
auto-detected as `avocado-verify` because that checkout tracks `avocado_verify.py`; `--file`
(relative to `--subject`) is required because `avocado-verify` works on one C file.

```sh
cd /home/jmsy/claudebox/avocado-agent
tmux new -s full-lz4 'eval/experiments/run.sh \
    --harness ../opus-5-5/full-avocado \
    --subject ../subject-programs/lz4_lib --file lz4.c \
    --profile /home/jmsy/avocado-profiles/opus-5-5.env \
    -n 3 -j 1 --timeout 14400 --memory 32g \
    2>&1 | tee /home/jmsy/avocado-profiles/full-lz4-opus-5-5.log'
```

- `-n 3` runs three times, `-j 1` one container at a time (budget memory as `-j` × `--memory`).
- `--timeout 14400` caps each run at four hours; a run that hits it gets exit code 124.
- The first line printed is `results: <dir>`; the batch directory is
  `~/.local/share/avocado-experiments/full-avocado-lz4_lib-opus-5-5/<timestamp>/`.

## 4. Check it took the right model

Each container first installs the treatment's Python package (about a minute), then prints the
runner and model. Confirm the first such line of run 1 says `model=claude-opus-5-5`:

```sh
grep -m1 'run-isolated: 2' ~/.local/share/avocado-experiments/full-avocado-lz4_lib-opus-5-5/*/run-1/run.log
```

## 5. Follow progress

The driver's log streams into `run.log` while the run is going:

```sh
tail -f ~/.local/share/avocado-experiments/full-avocado-lz4_lib-opus-5-5/*/run-1/run.log
```

## 6. Read the results

Each `run-<i>/` holds:

- `subject/` with the specified `lz4.c` and anything the agent wrote;
- `subject/lz4-avocado-verify.jsonl` with one record per function and a final `run_summary`;
- `transcripts/` with one Claude Code transcript per session (several per function);
- `command.txt`, `run.log` and `exit-code` (`2` means `avocado-verify` stopped on a usage limit).

A quick per-function verdict table for run 1:

```sh
jq -r 'select(.type == null) | "\(.outcome)\t\(.function)"' \
    ~/.local/share/avocado-experiments/full-avocado-lz4_lib-opus-5-5/*/run-1/subject/lz4-avocado-verify.jsonl
```

## Other treatments

The same command runs the other treatments by changing `--harness`:

| `--harness`                                   | runner           | note                                                        |
|-----------------------------------------------|------------------|-------------------------------------------------------------|
| `../opus-5-5/full-avocado`                    | `avocado-verify` |                                                             |
| `../opus-5-5/avocado-minus-docs`              | `avocado-verify` |                                                             |
| `../opus-5-5/avocado-minus-tools`             | `avocado-verify` |                                                             |
| `../opus-5-5/avocado-minus-mutation-testing`  | `avocado-verify` | no `.claude/settings.json`: the profile's `AVOCADO_MODEL` is the only thing setting the model |
| `../opus-5-5/avocado-minus-harness`           | `claude`         | no driver, so one `claude` session with the prompt `Verify /app/lz4_lib/lz4.c` |
| `../opus-5-5/pure-agentic-baseline` (or none) | `claude`         | the `AGENTS.md` prompt; `--file` is optional                |

Other subjects: `2048-clone` (`2048.c`), `kilo` (`kilo.c`) and `Syzygy_Zopfli` (`zopfli.c`).
