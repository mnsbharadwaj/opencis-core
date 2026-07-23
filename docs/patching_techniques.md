# Git Patching Guide: Applying `new_cci_commands` to Another Repository

This guide outlines how to apply the commits made on the `new_cci_commands` branch to another copy or clone of the `opencis-core` repository. 

Our branch diverged from the base branch `v0.5-dev` (at commit `a079e9d`) and contains the following 7 commits:
1. `2c20ed5` - Implement missing and stubbed CXL FM API CCI commands as per CXL Spec 4.0
2. `bdd3260` - Register all newly implemented CXL Fabric Manager API commands on Switch CCI executor
3. `3f7be37` - Add CXL Fabric Manager API implementation details and limitations documentation
4. `cc44233` - Expand cxl_commands_implementation_details.md with deep analysis of Group B limitations and simulated values
5. `2523daf` - Update DCD commands classification to Group B in documentation
6. `14d55e8` - Update Get LD Info and Get LD Allocations commands classification to Group B in documentation
7. `510a084` - Implement Fabric Manager CLI subcommands and Socket.IO client/server endpoints for the 22 new FM CCI commands

---

## Method 1: Exporting and Applying Patches (File-Based Transfer)

This method is ideal if the target repository is in a separate network, virtual machine, or if you want to share the changes as files (via email, USB, or chat).

### Step 1: Export Patches from Source Repository
In your local `opencis-core` workspace where `new_cci_commands` is checked out, run:
```bash
git format-patch v0.5-dev..new_cci_commands -o patches/
```
*This exports all 7 commits as numbered `.patch` files inside a new directory called `patches/`.*

### Step 2: Transfer Patches to Target Repository
Copy the entire `patches/` folder to the root directory of your target repository.

### Step 3: Check Patch Compatibility and Dry Run
In the target repository, switch to the target branch (e.g., `v0.5-dev` or a new branch created from it) and run:
```bash
# Check info about the patches
git apply --stat patches/*.patch

# Dry-run to check for merge conflicts before applying
git apply --check patches/*.patch
```
*If `git apply --check` returns no output/errors, the patches can be applied cleanly.*

### Step 4: Apply Patches in Order
Use `git am` (Git Apply Mailbox) to apply the patches while preserving original commit messages, authors, and timestamps:
```bash
git am patches/*.patch
```

### Step 5: Handling Conflicts (If Any)
If `git am` encounters conflicts, it will stop and print a message. To resolve:
1. Open the conflicted files indicated by Git and resolve standard conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
2. Stage the resolved files:
   ```bash
   git add <resolved-file>
   ```
3. Continue the patch application:
   ```bash
   git am --continue
   ```
*If you want to abort the patch application at any time, run `git am --abort`.*

---

## Method 2: Multi-Remote Cherry-Picking (Network/Local Directory Transfer)

This method is ideal if both repositories are local on the same machine, or if the target machine has network access to the source repository's remote Git server.

### Step 1: Add Source Repository as a Remote in Target Repository
Open a terminal in the target repository and add the source repository as a remote named `source_repo`:
```bash
# If the source repo is on the same local filesystem:
git remote add source_repo /path/to/source/opencis-core

# Or if it is hosted on GitHub:
git remote add source_repo https://github.com/mnsbharadwaj/opencis-core.git
```

### Step 2: Fetch Remote Branches
Fetch the commit history from the newly added remote:
```bash
git fetch source_repo
```

### Step 3: Create a Target Branch
Create a new branch in the target repository where you want to apply the commits:
```bash
git checkout -b new_cci_commands_patched
```

### Step 4: Cherry-Pick the Range of Commits
Cherry-pick the range of commits from `v0.5-dev` up to `new_cci_commands` on the remote:
```bash
git cherry-pick source_repo/v0.5-dev..source_repo/new_cci_commands
```
*This automatically copies all 7 commits onto your current branch, maintaining original authorship.*

---

## Step 5: Verification

After applying the changes using either method, verify that the application compiles and all tests pass:
```bash
# Run pytest verification
uv run --python 3.12 pytest tests/test_new_cci_commands.py -v
```
