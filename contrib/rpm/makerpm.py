#!/usr/bin/python

import git
import sys
import os
import os.path
import time
import subprocess

try:
    repo = git.Repo(os.getcwd())
    head_hash = repo.head.commit.hexsha[:7]
except git.InvalidGitRepositoryError:
    print("Not a git repository.  Aborting.")
    sys.exit(1)

now = time.strftime('%a %b %d %Y')
version = '3.3'
build = '0'
if len(sys.argv) > 1:
    build = sys.argv[1]

archive_filename = f'sopel-{version}.tar'
spec_filename_in = 'sopel.spec.in'
spec_filename_out = 'sopel.spec'
rpmbuild_target_dir = 'noarch'

print('Generating archive...')
with open(archive_filename, 'wb') as f:  # Open in binary write mode
    repo.archive(f, prefix=f'sopel-{version}/')

print('Building spec file..')
try:
    with open(spec_filename_in, 'r') as spec_in, open(spec_filename_out, 'w') as spec_out:
        for line in spec_in:
            newline = line.replace('#GITTAG#', head_hash)
            newline = newline.replace('#BUILD#', build)
            newline = newline.replace('#LONGDATE#', now)
            newline = newline.replace('#VERSION#', version)
            spec_out.write(newline)
except FileNotFoundError as e:
        print(f"Error: {e}. Aborting.")
        os.remove(archive_filename)
        sys.exit(1)

print('Starting rpmbuild...')
cmdline = f'rpmbuild --define="%_specdir {os.getcwd()}" --define="%_rpmdir {os.getcwd()}" --define="%_srcrpmdir {os.getcwd()}" --define="%_sourcedir {os.getcwd()}" -ba {spec_filename_out}'
try:
    subprocess.check_call(cmdline, shell=True)
except subprocess.CalledProcessError as e:
    print(f"Error during rpmbuild: {e}")
    sys.exit(1)

if os.path.exists(rpmbuild_target_dir):
    print("Moving RPM files to the current directory...")
    for item in os.listdir(rpmbuild_target_dir):
        os.rename(os.path.join(rpmbuild_target_dir, item), item)
    print('Cleaning...')
    try:
        os.rmdir(rpmbuild_target_dir)
    except OSError as e:
        print(f"Warning: Could not remove directory {rpmbuild_target_dir}: {e}")
else:
    print(f"Warning: Directory {rpmbuild_target_dir} not found.  No RPMs moved.")

print('Cleaning...')
os.remove(spec_filename_out)
os.remove(archive_filename)

print('Done')
