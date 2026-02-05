"""Execute Python scripts as subprocess tasks."""

import subprocess
import time
import json
import os
from pathlib import Path
from typing import Optional, Tuple, Dict, List

from ..secrets import secrets_service


def execute_script(
    script_path: Optional[str] = None,
    script_content: Optional[str] = None,
    params: Optional[Dict] = None,
    timeout: int = 300,
    env: Optional[Dict] = None,
    workspace: Optional[str] = None,
    required_secrets: Optional[List[str]] = None,
    task_id: Optional[int] = None
) -> Tuple[bool, str, Dict]:
    """Execute a Python script with subprocess.

    Args:
        script_path: Path to script (absolute or relative to workspace)
        script_content: Inline Python code to execute (alternative to script_path)
        params: Parameters to pass as JSON to script stdin
        timeout: Timeout in seconds
        env: Environment variables to add
        workspace: Working directory (defaults to cwd)
        required_secrets: List of secret keys to inject from DB
        task_id: Task ID for temp file naming

    Returns:
        Tuple of (success, output, metadata)
        - success: True if script exited with code 0
        - output: stdout on success, error message on failure
        - metadata: dict with returncode, duration, stdout_length, stderr_length
    """
    workspace_path = Path(workspace) if workspace else Path.cwd()
    tmp_script_path = None

    if script_content:
        # Write inline script to temp file
        tmp_script_path = Path(f"/tmp/otomata_task_{task_id or 'unknown'}.py")
        tmp_script_path.write_text(script_content)
        full_path = tmp_script_path
    elif script_path:
        full_path = Path(script_path)
        if not full_path.is_absolute():
            full_path = workspace_path / script_path
        if not full_path.exists():
            return False, f"Script not found: {script_path}", {}
    else:
        return False, "No script_path or script_content provided", {}

    # Build command - use venv python if available
    venv_python = workspace_path / 'venv' / 'bin' / 'python3'
    if not venv_python.exists():
        venv_python = workspace_path / 'app' / 'venv' / 'bin' / 'python3'

    if venv_python.exists():
        python_cmd = str(venv_python)
    else:
        python_cmd = 'python3'

    cmd = [python_cmd, str(full_path)]

    # Prepare minimal environment
    exec_env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': os.environ.get('HOME', '/tmp'),
    }

    # Add PYTHONPATH
    pythonpath = str(workspace_path)
    if (workspace_path / 'app').exists():
        pythonpath = f"{workspace_path / 'app'}:{pythonpath}"
    exec_env['PYTHONPATH'] = pythonpath

    # Copy DATABASE_URL if set
    if os.environ.get('DATABASE_URL'):
        exec_env['DATABASE_URL'] = os.environ['DATABASE_URL']

    # Inject requested secrets from DB
    if required_secrets:
        try:
            secrets = secrets_service.get_for_task(required_secrets)
            exec_env.update(secrets)
        except Exception:
            pass  # Script might handle missing secrets

    # Add custom env vars (override)
    if env:
        exec_env.update(env)

    t0 = time.time()

    try:
        # Pass params as JSON to stdin
        input_data = json.dumps(params) if params else None

        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=exec_env,
            cwd=str(workspace_path)
        )

        duration = time.time() - t0

        metadata = {
            'returncode': result.returncode,
            'duration': duration,
            'stdout_length': len(result.stdout),
            'stderr_length': len(result.stderr)
        }

        if result.returncode == 0:
            return True, result.stdout, metadata
        else:
            error_msg = f"Script exited with code {result.returncode}\nSTDERR:\n{result.stderr}"
            return False, error_msg, metadata

    except subprocess.TimeoutExpired:
        duration = time.time() - t0
        return False, f"Script timeout after {timeout}s", {'duration': duration, 'timeout': True}
    except Exception as e:
        duration = time.time() - t0
        return False, f"Script execution error: {e}", {'duration': duration, 'error': str(e)}
    finally:
        # Cleanup temp script file
        if tmp_script_path and tmp_script_path.exists():
            tmp_script_path.unlink()
