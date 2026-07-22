# Local Coding Agent Setup

Local AI coding assistant using Ollama and Aider with qwen2.5-coder:7b

## Prerequisites

- Windows 10/11
- 4GB+ RAM (8GB+ recommended)
- [Ollama](https://ollama.ai) installed
- Administrator access (recommended)

## Installation

Run the setup script:

`powershell
# From PowerShell
.\Setup-CodingAgent.ps1
`

## What gets installed

- Ollama model: qwen2.5-coder:7b
- Custom Ollama profile: local-code:7b
- uv Python tool manager
- ider-chat using Python 3.11
- Helper scripts:
  - Test-Ollama.ps1
  - Start-Aider.ps1
  - Start-Aider-Here.ps1

## Usage

Test Ollama:

`powershell
.\Test-Ollama.ps1
`

Start Aider in a specific project:

`powershell
.\Start-Aider.ps1 C:\Coding\my-project
`

Start Aider in the current directory:

`powershell
.\Start-Aider-Here.ps1
`

## Safety notes

- Both launcher scripts always pass --edit-format diff to Aider. This is
  not optional. Local/custom models like local-code:7b are unrecognized
  by Aider's model registry, so without this flag Aider silently falls
  back to "whole" edit format, where the model must retype an entire file
  to change anything. A 7B model under load can drop existing code when
  doing this. "diff" mode only ever touches lines that actually change.
- Both launchers also pass --no-gitignore so they never block waiting
  for interactive keyboard input on the "Add .aider* to .gitignore?" prompt.

## Notes

- Make sure Ollama is running before using the helper scripts:
  `powershell
  ollama serve
  `
- The setup uses the local-code:7b model name for Aider.
- If you change the Ollama host, update the OLLAMA_HOST environment variable accordingly.
