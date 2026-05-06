#!/usr/bin/env python3
"""Render build.yaml from build.yaml.tpl using values from benchmark_config.yaml.

Usage:
    python3 scripts/render_build_yaml.py [config_path] [template_path] [output_path]
"""

import sys
import yaml


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/benchmark_config.yaml"
    template_path = sys.argv[2] if len(sys.argv) > 2 else "build.yaml.tpl"
    output_path = sys.argv[3] if len(sys.argv) > 3 else "build.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    deployment = config.get("deployment", {})

    replacements = {
        "{{POD_NAME}}": deployment.get("pod_name", "yc-vllm-spyre-benchmark"),
        "{{SPYRE_PF_CARDS}}": str(deployment.get("spyre_pf_cards", 1)),
        "{{BENCHMARK_REPO}}": deployment.get("benchmark_repo", "https://github.com/YashasviChaurasia/spyre-benchmark-suite.git"),
        "{{BENCHMARK_BRANCH}}": deployment.get("benchmark_branch", "main"),
        "{{SPYRE_INFERENCE_REPO}}": deployment.get("spyre_inference_repo", "https://github.com/torch-spyre/spyre-inference.git"),
        "{{SPYRE_INFERENCE_BRANCH}}": deployment.get("spyre_inference_branch", "main"),
    }

    with open(template_path) as f:
        template = f.read()

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    with open(output_path, "w") as f:
        f.write(rendered)

    print(f"Rendered {output_path} (pod: {replacements['{{POD_NAME}}']}, "
          f"cards: {replacements['{{SPYRE_PF_CARDS}}']}, "
          f"branch: {replacements['{{BENCHMARK_BRANCH}}']})")


if __name__ == "__main__":
    main()
