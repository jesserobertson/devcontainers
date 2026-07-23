from __future__ import annotations

from pydantic import BaseModel, ConfigDict

FeatureOptions = dict[str, bool | int | str]
LifecycleCommand = str | list[str] | dict[str, str | list[str]]


class DevContainerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    image: str | None = None
    workspaceFolder: str | None = None
    workspaceMount: str | None = None
    features: dict[str, FeatureOptions] = {}
    runArgs: list[str] = []
    mounts: list[str] = []
    forwardPorts: list[int | str] = []
    remoteEnv: dict[str, str] = {}
    containerEnv: dict[str, str] = {}
    postCreateCommand: LifecycleCommand | None = None
    postStartCommand: LifecycleCommand | None = None
    postAttachCommand: LifecycleCommand | None = None
    onCreateCommand: LifecycleCommand | None = None
    updateContentCommand: LifecycleCommand | None = None
    initializeCommand: LifecycleCommand | None = None
    waitFor: str | None = None
    remoteUser: str | None = None
    shutdownAction: str | None = None
