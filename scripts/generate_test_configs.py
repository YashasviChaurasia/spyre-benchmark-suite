#!/usr/bin/env python3
"""Convert benchmark_config.yaml to vLLM-compatible JSON test files.

Produces latency-tests.json and throughput-tests.json in the same format
used by upstream vLLM (.buildkite/performance-benchmarks/tests/).

Usage:
    python generate_test_configs.py [config_path] [output_dir]
"""

import json
import os
import sys

import yaml


def sanitize(name: str) -> str:
    return name.replace("/", "_").replace("-", "_").lower()


def generate_latency_tests(config: dict) -> list[dict]:
    tests = []
    engine = config["engine"]
    for model in config["models"]:
        for workload in config["workloads"]["latency"]:
            test_name = (
                f"latency_{sanitize(model['name'])}"
                f"_in{workload['input_len']}"
                f"_out{workload['output_len']}"
                f"_bs{workload['batch_size']}"
            )
            params = {
                "model": model["name"],
                "tensor_parallel_size": model["tensor_parallel_size"],
                "input_len": workload["input_len"],
                "output_len": workload["output_len"],
                "batch_size": workload["batch_size"],
                "num_iters_warmup": workload["num_iters_warmup"],
                "num_iters": workload["num_iters"],
                "dtype": engine["dtype"],
                "max_model_len": engine["max_model_len"],
                "load_format": engine["load_format"],
            }
            if engine.get("enforce_eager"):
                params["enforce_eager"] = True

            tests.append({"test_name": test_name, "parameters": params})
    return tests


def generate_throughput_tests(config: dict) -> list[dict]:
    tests = []
    engine = config["engine"]
    for model in config["models"]:
        for workload in config["workloads"]["throughput"]:
            test_name = (
                f"throughput_{sanitize(model['name'])}"
                f"_in{workload['input_len']}"
                f"_out{workload['output_len']}"
                f"_n{workload['num_prompts']}"
            )
            params = {
                "model": model["name"],
                "tensor_parallel_size": model["tensor_parallel_size"],
                "input_len": workload["input_len"],
                "output_len": workload["output_len"],
                "num_prompts": workload["num_prompts"],
                "backend": "vllm",
                "dtype": engine["dtype"],
                "max_model_len": engine["max_model_len"],
                "load_format": engine["load_format"],
            }
            if engine.get("enforce_eager"):
                params["enforce_eager"] = True

            tests.append({"test_name": test_name, "parameters": params})
    return tests


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/benchmark_config.yaml"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "results"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    os.makedirs(output_dir, exist_ok=True)

    latency_tests = generate_latency_tests(config)
    throughput_tests = generate_throughput_tests(config)

    latency_path = os.path.join(output_dir, "latency-tests.json")
    with open(latency_path, "w") as f:
        json.dump(latency_tests, f, indent=4)

    throughput_path = os.path.join(output_dir, "throughput-tests.json")
    with open(throughput_path, "w") as f:
        json.dump(throughput_tests, f, indent=4)

    print(f"Generated {len(latency_tests)} latency tests -> {latency_path}")
    print(f"Generated {len(throughput_tests)} throughput tests -> {throughput_path}")


if __name__ == "__main__":
    main()
