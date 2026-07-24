# Git Patching Guide: Applying `dcd_qos_less_FM` to Another Repository

This guide outlines how to apply the commits made on the `dcd_qos_less_FM` branch to another copy or clone of the `opencis-core` repository. 

Our branch diverged from the base branch `v0.5-dev` and contains separate commits for each Fabric Manager API command set (Physical Switch, Virtual Switch, MLD Port, Multi-Headed Device, DCD Management).

---

## Method 1: Exporting and Applying Patches (File-Based Transfer)

This method is ideal if the target repository is in a separate network, virtual machine, or if you want to share the changes as files (via email, USB, or chat).

### Step 1: Export Patches from Source Repository
In your local `opencis-core` workspace where `new_cci_commands` is checked out, run:
```bash
git format-patch v0.5-dev..dcd_qos_less_FM -o patches/
```
*This exports all commits as numbered `.patch` files inside a new directory called `patches/`.*

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
git checkout -b dcd_qos_less_FM_patched
```

### Step 4: Cherry-Pick the Range of Commits
Cherry-pick the range of commits from `v0.5-dev` up to `dcd_qos_less_FM` on the remote:
```bash
git cherry-pick source_repo/v0.5-dev..source_repo/dcd_qos_less_FM
```
*This automatically copies all commits onto your current branch, maintaining original authorship.*

---

## Step 5: Verification

After applying the changes using either method, verify that the application compiles and all tests pass:
```bash
# Run pytest verification
uv run --python 3.12 pytest tests/test_new_cci_commands.py -v
```
