"""
Git-based Checkpoints implementations.

Requires setting environment variables with GIT config and environment name
(if you are implementing in multiple environments in the same git repo)

e.g.
DEPLOY_ENV=test

GIT_USER=#git login
GIT_PASS=#password
GIT_EMAIL=#some email
GIT_URL=#remote url

Optional:
DEBUG_HOME=#replace default home (used with JupyterHub) with a custom dir

"""
import os
import shutil

from tornado.web import HTTPError

from notebook.services.contents.checkpoints import (
    Checkpoints,
    GenericCheckpointsMixin,
)
from notebook.services.contents.fileio import FileManagerMixin

from jupyter_core.utils import ensure_dir_exists
from ipython_genutils.py3compat import getcwd
from traitlets import Unicode

from notebook import _tz as tz

import traceback
from brigit import Git, GitException
import pytz

N_CHECKPOINTS = 10
PYTZ_TIMEZONE = "America/New_York"

class CustomCheckpoints(Checkpoints):
    '''
        Using Git means that we only ever need to do one operation per file
        Here we replace the Jupyter defaults to loop over all checkpoints
    '''
    def rename_all_checkpoints(self, old_path, new_path):
        """Rename all checkpoints for old_path to new_path."""
        #we don't actually care about the commit hash
        self.rename_checkpoint('', old_path, new_path)
    def delete_all_checkpoints(self,path):
        """Delete all checkpoints for the given path."""
        self.delete_checkpoint('', path)
        
            

class GitCheckpoints(CustomCheckpoints):
    """
    A Checkpoints that commits checkpoints for files to a git repo.
    """
    root_dir = Unicode(config=True)

    env = os.environ['DEPLOY_ENV'] #need to pass this through to notebooks in jupyterhub config
    name = os.environ['USER'].upper()

    if 'DEBUG_HOME' in os.environ.keys():
        home = os.environ['DEBUG_HOME']
    else:
        home = os.environ['HOME']

    print('Init Git wrapper in {}'.format(home))
    git = Git(home)

    try:
        user,pw,email, = os.environ['GIT_USER'],os.environ['GIT_PASS'],os.environ['GIT_EMAIL']
        repo_url = os.environ['GIT_URL']
    except KeyError:
        print('GIT Env Variables not set properly. Assuming local Git only')
        traceback.print_exc()
        user, pw, email, repo_url = '','','',''

    N_CHECKPOINTS=10

    try:
        git.status()
        init=True
        branch = git("rev-parse",'--abbrev-ref','HEAD').replace('\n','')
    except GitException:
        init=False
        #default to USER-ENV for branch name.
        #when used with JupyterHub, assume users are only using branch-names they are meant to
        #TODO: restrict branch naming scheme
        branch = '{}-{}'.format(name,env)

    if not init:
        repo='http://{}:{}@{}'.format(user,pw,repo_url)
        git.init()
        if user and pw and repo_url:
            git.remote('add','origin',repo)        
            print("Git checkpoints connecting to remote repo ...")
        try: 
            print("Assume branch already exists, fetch, link branches and checkout")
            git.fetch('origin',branch)
            git.branch(branch,'origin/{}'.format(branch))
            git.checkout(branch)
        except GitException:
            print("Create branch if not existing in remote")
            git.checkout('-b',branch)

        #Create a .gitignore to ignore hidden folders
        with open(home+'/.gitignore','w') as f:
            f.write('.*\n!/.gitignore')
        
        try:
            git.add('.')
            git.commit('-m','Init checkpoints for existing untracked files')
            try:
                git.push('--set-upstream','origin',branch)
            except: #this might fail with no remote
                pass
            git.push()
        except GitException: #might only have gitignore in the repo
            pass

    #Set git config
    git.config('user.name',name)
    git.config('user.email',email)
    git.config('push.default','matching')

    print('GitCheckpoints initialised')

    def _root_dir_default(self):
        try:
            return self.parent.root_dir
        except AttributeError:
            return getcwd()

    # ContentsManager-dependent checkpoint API
    def create_checkpoint(self, contents_mgr, path):
        """Create a checkpoint."""
        path = self.checkpoint_path('',path)
        self.log.debug(
            "Creating checkpoint %s",
            path,
        )

        self.git.add(path)
        try:
            self.git.commit('-m','Checkpoint {}'.format(path),path)
            
        except GitException: #no changes, perhaps?
            pass
        
        stats = [i for i in self.git.pretty_log('-1')][0]
        checkpoint_id, datetime = stats['hash'], stats['datetime']

        self.git.push('--set-upstream','origin',self.branch)
        self.git.push()
        
        return self.checkpoint_model(checkpoint_id, datetime)

    def restore_checkpoint(self, contents_mgr, checkpoint_id, path):
        """Restore a checkpoint."""
        path = self.checkpoint_path('',path)

        self.log.debug(
            "Restoring checkpoint %s -> %s",
            checkpoint_id,
            path,
        )

        #Commit any current changes locally
        try:
            self.git.add(path)
            self.git.commit('-m','Committing changes and restoring to {}'.format(checkpoint_id),path)
        except GitException: 
            pass

        #Restore file to commit hash
        self.git.checkout(checkpoint_id, path)

    # ContentsManager-independent checkpoint API
    def rename_checkpoint(self, checkpoint_id, old_path, new_path):
        """Rename a checkpoint from old_path to new_path."""
        old_cp_path = self.checkpoint_path(checkpoint_id, old_path)
        new_cp_path = self.checkpoint_path(checkpoint_id, new_path)
        
        self.log.debug(
            "Renaming checkpoint %s -> %s",
            old_cp_path,
            new_cp_path,
        )
        
        try:
            self.git.add(old_cp_path, new_cp_path)
            self.git.commit('-m','Renaming {} -> {}'.format(old_cp_path,new_cp_path),old_cp_path,new_cp_path)
        except GitException: #Fresh notebooks with no save
            self.git.add(new_cp_path)
            self.git.commit('-m','Renaming unsaved notebook -> {}'.format(new_cp_path),new_cp_path)
        self.git.push()

    def delete_checkpoint(self, checkpoint_id, path):
        """delete a file's checkpoint"""

        #in our Git context, this will just be committing the deleted file
        cp_path = self.checkpoint_path(checkpoint_id, path)

        self.log.debug(
            "Deleting checkpoint %s @ %s",
            cp_path,
            checkpoint_id,
        )

        self.git.add(cp_path)
        self.git.commit('-m','Deleting {}'.format(cp_path),cp_path)            
        self.git.push()

    def list_checkpoints(self, path):
        """
            list the latest checkpoints for a given file. 
            N_CHECKPOINTS is set globally above
        """
        path = self.checkpoint_path('',path)

        self.log.debug('List checkpoints for {}'.format(path))
        try:
            commit_log=self.git.pretty_log('-{}'.format(self.N_CHECKPOINTS),'--',path)
            stats = [(i['hash'],i['datetime']) for i in commit_log]
        except:
            traceback.print_exc()
            stats = []

        return [self.checkpoint_model(checkpoint_id, datetime) for (checkpoint_id, datetime) in stats]

    # Checkpoint-related utilities
    def checkpoint_path(self, checkpoint_id, path):
        """find the path to a checkpoint"""
        return os.path.join(self.home,path.strip('/'))

    def checkpoint_model(self, checkpoint_id, datetime):
        """construct the info dict for a given checkpoint"""
        local = pytz.timezone(PYTZ_TIMEZONE)
        local_dt = local.localize(datetime,is_dst=True)
        last_modified = local_dt.astimezone(pytz.utc)

        info = dict(
            id=checkpoint_id,
            last_modified=last_modified,
        )
        return info

    # Error Handling
    def no_such_checkpoint(self, path, checkpoint_id):
        raise HTTPError(
            404,
            u'Checkpoint does not exist: %s@%s' % (path, checkpoint_id)
        )