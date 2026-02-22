import re
import os
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Any
from src.service.github_service import GitHubService
from src.utils.logger import logger

class RepoOnboarderService:
    def __init__(self, target_org: Optional[str] = None):
        self.gh_service = GitHubService()
        self.target_org = target_org
        
        # If target_org is not provided, get the current user as default
        if not self.target_org:
            try:
                user_info = self.gh_service.get_current_user()
                self.target_owner = user_info['login']
            except Exception as e:
                logger.error(f"Failed to get current user: {e}")
                raise
        else:
            self.target_owner = self.target_org

    def onboard_repos(self, repo_list: List[Dict[str, Any]]):
        """
        Process a list of repositories to onboard.
        
        repo_list format:
        [
            {"workflow_url": "https://github.com/owner/repo/blob/main/.github/workflows/main.yml", ...},
            ...
        ]
        """
        logger.info(f"Starting onboarding for {len(repo_list)} repositories to owner '{self.target_owner}'...")

        for repo_config in repo_list:
            workflow_url = repo_config.get("workflow_url")
            source_url = repo_config.get("source_url") # Backward/Optional compatibility
            
            # 1. Infer source_url from workflow_url if not present
            if workflow_url and not source_url:
                # Expected format: https://github.com/owner/repo/blob/ref/path/to/file
                match = re.search(r"(https://github\.com/[^/]+/[^/]+)", workflow_url)
                if match:
                   source_url = match.group(1)
                   if not source_url.endswith(".git"):
                       source_url += ".git"
            
            if not source_url:
                logger.error(f"Skipping config: could not determine source repo from {repo_config}")
                continue

            # Determine target name (same as before)
            target_name = repo_config.get("target_name")
            if not target_name:
                target_name = source_url.rstrip("/").split("/")[-1].replace(".git", "")

            target_private = repo_config.get("private", False)
            enable_dispatch = repo_config.get("enable_dispatch", True)

            # Pass workflow_url so we know which file to patch
            self._process_single_repo(source_url, target_name, target_private, workflow_url, enable_dispatch)

    def _process_single_repo(self, source_url: str, target_name: str, private: bool, workflow_url: Optional[str] = None, enable_dispatch: bool = True):
        full_target_name = f"{self.target_owner}/{target_name}"
        
        # 1. Check if safely exists
        existing_repo = self.gh_service.get_repo(self.target_owner, target_name)
        
        if existing_repo:
            logger.info(f"Repository {full_target_name} already exists. Checking status...")
            
            # Check if it's empty
            if existing_repo.get("size", 0) > 0:
                return

            logger.info(f"Repository {full_target_name} is empty. Populating...")
            
            # Reuse the existing clone URL but authenticated
            clone_url = existing_repo.get("clone_url")
            authenticated_url = clone_url.replace("https://", f"https://{self.gh_service.token}@")
            
            # Logic for workflow path
            workflow_path = self._extract_workflow_path(workflow_url)
            
            try:
                self._mirror_git_content(source_url, authenticated_url, workflow_path, enable_dispatch)
                logger.info(f"Successfully populated {full_target_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to populate {full_target_name}: Command '{e.cmd}' returned {e.returncode}")
                if e.stdout:
                    logger.error(f"Stdout:\n{e.stdout.decode('utf-8', errors='replace')}")
                if e.stderr:
                    logger.error(f"Stderr:\n{e.stderr.decode('utf-8', errors='replace')}")
            except Exception as e:
                logger.error(f"Failed to populate {full_target_name}: {e}")
            return

        # 2. Create the repository
        logger.info(f"Creating repository {full_target_name}...")
        try:
            # Note: create_repo args: name, org, description, private
            # If self.target_org is set, pass it. If None, it creates under user.
            repo_info = self.gh_service.create_repo(
                name=target_name,
                org=self.target_org if self.target_org else None,
                description=f"Mirrored from {source_url}",
                private=private
            )
            clone_url = repo_info.get("clone_url") # HTTPS clone URL
            
            # Inject token into clone URL for push authentication
            # Format: https://TOKEN@github.com/owner/repo.git
            authenticated_url = clone_url.replace("https://", f"https://{self.gh_service.token}@")

            logger.info(f"Repository created. Mirroring code from {source_url}...")
            
            # Extract workflow path
            workflow_path = self._extract_workflow_path(workflow_url)
            
            self._mirror_git_content(source_url, authenticated_url, workflow_path, enable_dispatch)
            
            logger.info(f"Successfully onboarded {full_target_name}")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to onboard {full_target_name}: Command '{e.cmd}' returned {e.returncode}")
            if e.stdout:
                logger.error(f"Stdout:\n{e.stdout.decode('utf-8', errors='replace')}")
            if e.stderr:
                logger.error(f"Stderr:\n{e.stderr.decode('utf-8', errors='replace')}")
        except Exception as e:
            logger.error(f"Failed to onboard {full_target_name}: {e}")

    def _extract_workflow_path(self, workflow_url: Optional[str]) -> Optional[str]:
        if not workflow_url:
            return None
        parts = workflow_url.split("/blob/")
        if len(parts) > 1 and ".github/workflows" in parts[1]:
            sub_parts = parts[1].split(".github/workflows")
            return f".github/workflows{sub_parts[1]}"
        return None

    def _mirror_git_content(self, source_url: str, target_push_url: str, workflow_file_to_patch: Optional[str] = None, enable_dispatch: bool = True):
        """
        Clones only the single default branch (depth 1), optionally patches a workflow file, and pushes to target.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Clone only default branch (--single-branch) with no history (--depth 1)
            # This is faster and avoids cluttering the organization with all branches/tags
            logger.info("  Cloning source (default branch)...", extra={'markup': True})
            
            subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", source_url, "."],
                cwd=temp_path,
                check=True,
                capture_output=True
            )

            # Patch workflow file if requested
            if workflow_file_to_patch:
                if enable_dispatch:
                   self._patch_workflow_dispatch(temp_path, workflow_file_to_patch)
                
                # Cleanup other workflows to avoid triggering unwanted actions
                self._cleanup_other_workflows(temp_path, workflow_file_to_patch)

            # We need to remove the original origin remote and add the new one
            logger.info("  Pushing to new repository...", extra={'markup': True})
            subprocess.run(["git", "remote", "remove", "origin"], cwd=temp_path, check=True)
            subprocess.run(["git", "remote", "add", "origin", target_push_url], cwd=temp_path, check=True)
            
            # Push the current branch (HEAD) to the remote integration branch.
            # Use --force because the history might be rewritten (e.g. we are re-patching) 
            # or divergent if the source repo force-pushed.
            try:
                subprocess.run(
                    ["git", "push", "-u", "-f", "origin", "HEAD"],
                    cwd=temp_path,
                    check=True,
                    capture_output=True
                )
            except subprocess.CalledProcessError:

                logger.warning("  Shallow push failed. Retrying with fresh git history (squash)...")
                
                # Remove old .git
                import shutil
                import stat

                def remove_readonly(func, path, _):
                    "Clear the readonly bit and reattempt the removal"
                    try:
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    except Exception as e:
                        logger.warning(f"Failed to remove {path}: {e}")

                # Handle Windows read-only git objects
                git_dir = temp_path / ".git"
                if git_dir.exists():
                     shutil.rmtree(git_dir, onerror=remove_readonly)
                
                # Re-init
                subprocess.run(["git", "init"], cwd=temp_path, check=True, capture_output=True)
                subprocess.run(["git", "checkout", "-b", "main"], cwd=temp_path, check=True, capture_output=True)
                
                # Re-add remote
                subprocess.run(["git", "remote", "add", "origin", target_push_url], cwd=temp_path, check=True)
                
                # Add all files
                subprocess.run(["git", "add", "."], cwd=temp_path, check=True, capture_output=True)
                
                # Check for executable files (.sh, .ts, .py) and set execute permissions
                # We specifically look for .sh files which are common build scripts
                # On Windows, git add might add them as 644. We force update index to 755
                # Find all .sh files
                sh_files = list(temp_path.rglob("*.sh"))
                bin_files = list(temp_path.rglob("bin/*")) # common convention
                executable_candidates = sh_files + bin_files
                
                if executable_candidates:
                    logger.info(f"  Restoring executable permissions for {len(executable_candidates)} files...", extra={'markup': True})
                    for file in executable_candidates:
                        if file.is_file():
                           try:
                               rel_path = file.relative_to(temp_path).as_posix()
                               subprocess.run(
                                   ["git", "update-index", "--chmod=+x", rel_path],
                                   cwd=temp_path,
                                   check=False, # Don't crash if one fails
                                   capture_output=True
                               )
                           except Exception:
                               pass

                # Commit
                env = os.environ.copy()
                if not env.get("GIT_AUTHOR_NAME"):
                    env["GIT_AUTHOR_NAME"] = "Bolt Onboarder"
                if not env.get("GIT_AUTHOR_EMAIL"):
                    env["GIT_AUTHOR_EMAIL"] = "bolt-onboarder@bolt.local"
                if not env.get("GIT_COMMITTER_NAME"):
                    env["GIT_COMMITTER_NAME"] = "Bolt Onboarder"
                if not env.get("GIT_COMMITTER_EMAIL"):
                    env["GIT_COMMITTER_EMAIL"] = "bolt-onboarder@bolt.local"
                
                subprocess.run(
                    ["git", "commit", "-m", "Initial mirror commit (squashed)"],
                    cwd=temp_path,
                    check=True,
                    capture_output=True,
                    env=env
                )
                
                # Push force
                subprocess.run(
                    ["git", "push", "-u", "-f", "origin", "main"],
                    cwd=temp_path,
                    check=True,
                    capture_output=True
                )

    def _patch_workflow_dispatch(self, repo_path: Path, relative_file_path: str):
        """
        Injects 'workflow_dispatch:' into the on: section of the workflow file.
        """
        # remove leading slash if any
        relative_file_path = relative_file_path.lstrip("/")
        file_path = repo_path / relative_file_path
        
        if not file_path.exists():
            logger.warning(f"  Workflow file {relative_file_path} not found in repo at {file_path}. Skipping patch.")
            return

        logger.info(f"  Patching {relative_file_path} with workflow_dispatch...", extra={'markup': True})
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Simple heuristic replacement: 
            # Find "on:" section and check if workflow_dispatch is missing.
            # If so, add it.
            
            if "workflow_dispatch" not in content:
                new_content = content
                
                # Regex patterns to find "on:"
                # 1. on: [push, pull_request]
                # 2. on:
                #      push:
                
                if re.search(r"on:\s*\[", content):
                     # e.g., on: [push] -> on: [push, workflow_dispatch]
                     new_content = re.sub(r"(on:\s*\[.*?)(\])", r"\1, workflow_dispatch]", content, count=1)
                
                elif re.search(r"on:\s*\n", content):
                    # e.g., on:
                    #   push: ...
                    # -> on:
                    #   workflow_dispatch:
                    #   push: ...
                    new_content = re.sub(r"(on:\s*\n)", r"\1  workflow_dispatch:\n", content, count=1)
                
                else:
                    # Fallback for simple "on: string" cases or complex ones not covered
                    # Just trying to force inject after "on:"
                    new_content = re.sub(r"(on:.*)", r"\1\n  workflow_dispatch:", content, count=1)

                if new_content != content:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    
                    # Configure git user for commit if not set
                    env = os.environ.copy()

                    subprocess.run(
                        ["git", "add", relative_file_path], cwd=repo_path, check=True, capture_output=True
                    )
                    subprocess.run(
                        ["git", "commit", "-m", "chore: Enable manual workflow_dispatch execution"], 
                        cwd=repo_path, 
                        check=True, 
                        capture_output=True,
                        env=env
                    )
                    logger.info("  Patch applied and committed.")
            else:
                logger.info("  workflow_dispatch already present.")

        except Exception as e:
            logger.warning(f"  Failed to patch workflow file: {e}")

    def _cleanup_other_workflows(self, repo_path: Path, keep_workflow_rel_path: str):
        """
        Deletes all files in .github/workflows EXCEPT the target one.
        """
        keep_path_obj = (repo_path / keep_workflow_rel_path.lstrip("/")).resolve()
        workflows_dir = keep_path_obj.parent
        
        if not workflows_dir.exists():
            return
        
        deleted_count = 0
        file_list = list(workflows_dir.iterdir())
        
        # We need git env vars for commit
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Bolt Runner"
        env["GIT_AUTHOR_EMAIL"] = "bolt@runner.local"
        env["GIT_COMMITTER_NAME"] = "Bolt Runner"
        env["GIT_COMMITTER_EMAIL"] = "bolt@runner.local"

        for item in file_list:
            if item.is_file() and (item.suffix == '.yml' or item.suffix == '.yaml'):
                try:
                    resolved_item = item.resolve()
                    # Skip the file we want to keep
                    if resolved_item == keep_path_obj:
                        continue
                        
                    # Delete the file
                    item.unlink()
                    deleted_count += 1
                    
                    # Remove from git index
                    subprocess.run(
                        ["git", "rm", item.name], 
                        cwd=workflows_dir, 
                        check=False, 
                        capture_output=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete extra workflow {item.name}: {e}")
        
        if deleted_count > 0:
            logger.info(f"  Removed {deleted_count} extra workflow files.", extra={'markup': True})
            # Commit the deletions
            try:
                subprocess.run(
                    ["git", "commit", "-m", "chore: Remove unused workflows"],
                    cwd=repo_path,
                    check=False,
                    capture_output=True,
                    env=env
                )
            except Exception as e:
                logger.warning(f"Failed to commit workflow deletions: {e}")
