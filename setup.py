from setuptools import setup, find_packages

setup(
    name="linux-terminal-agent",
    version="1.0.0",
    author="nimajavan",
    description="Natural Language to Linux Terminal Automation with Modular AI Backends",
    long_description=open("README.md", encoding="utf-8").read() if open("README.md") else "",
    long_description_content_type="text/markdown",
    url="https://github.com/nimajavan/linux-terminal-agent",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Environment :: Console",
    ],
    python_requires=">=3.8",
    scripts=["lta"],
    entry_points={
        "console_scripts": [
            "lta=terminal_agent.cli:main",
        ],
    },
)
