### env

Python 3.12.12
rustc 1.95.0 (59807616e 2026-04-14)

### steps

0. install requirements and api
```
pip install -r requirements.txt
```

You need to change the `api_key` and `model_name` in `./config.example.json` to your own api key and model name, then rename it to `config.json`.

1. start translation
```
./scripts/agent.sh [repo_name]
```

eg:
```
./scripts/agent.sh c4
```

output dir is ./output/[repo_name]
note: some repos you may need to rebuild, because these repos have hard-coded path.

If you want to test a new C project, place the C project under the `datasets/` directory, and put the test scripts (shell scripts) into the `test/` directory under the C project's root. Make sure the C project can be compiled into an executable binary, because our testing logic compares the behavior of the compiled C and Rust binaries. Both the C and Rust binaries must be able to execute the test shell script.

2. check unsafe rate/clippy

You can use 'python ./scripts/get_unsafe_rate.py [repo_name]' and './scripts/clippy_check.sh [repo_name]' to get unsafe rate and clippy rate.