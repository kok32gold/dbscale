# Install

DBScale is a Python package. The core does not call an external DBScale service.

## Requirements

- Python 3.11+
- Docker, if you want DBScale to create the sandbox for you
- A PostgreSQL database to inspect (your own, or the demo in `examples/postgres`)

An LLM key is not required. A cloud account is not required.

## From a clone

```bash
git clone https://github.com/kok32gold/dbscale.git
cd dbscale
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"    # contributors
# or
pip install -e .           # run only
dbscale --version
```

There is no published package. Use the clone above.

## Check the install

```bash
dbscale --help
dbscale init --name hello
```

`dbscale init` writes `dbscale.yaml` and `queries/example.sql`. It does not
contact a network service.
