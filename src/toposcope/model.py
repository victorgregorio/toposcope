from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, TypedDict


class Node(TypedDict, total=False):
    id: str
    kind: str
    label: str
    properties: Dict[str, str]


class Edge(TypedDict, total=False):
    id: str
    source: str
    target: str
    kind: str
    label: str


class Graph(TypedDict):
    nodes: List[Node]
    edges: List[Edge]
