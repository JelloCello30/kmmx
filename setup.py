"""Compatibility shim for editable installs with pip versions before PEP 660."""

from setuptools import find_packages, setup


setup(
    name="kmmx",
    version="0.1.0",
    description="Risk-first, incentive-aware Kalshi market-making bot",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    extras_require={
        "live": ["cryptography>=42.0"],
        "dev": ["pytest>=8.0", "ruff>=0.5"],
    },
    entry_points={"console_scripts": ["kmmx=kmmx.cli:main"]},
)

