
from cProfile import run
from decimal import ROUND_DOWN
import os
import sys
import tempfile

import shutil
import numpy as np
import parmed as pmd

#from utils import lig_prep, ligs_prep, ligs_copy_allformat


GMXEXE='gmx'
MDPFILESDIR = os.path.dirname(os.path.abspath(__file__)) + '/mdp'
NUM_OF_THREAD = 4
def convert_to_pdb(inputfile, filetype, outfile=None):
    if outfile is None:
        filename = os.path.split(inputfile)[-1][:-4]
        outfile = filename + '.pdb'
    # convert to mol2
    cmd = 'obabel -i %s %s -o pdb -O %s'%(filetype, inputfile, outfile)
    RC = os.system(cmd)
    if RC!=0:
        raise Exception('ERROR: failed convert %s to %s'%(inputfile, outfile))
    return outfile

def guess_filetype(inputfile):
    """Guess the file type of input file.

    Args:
        inputfile ([str]): A input file to guess filetype

    Returns:
        [str]: A filetype
    """
    basefile = os.path.split(inputfile)[-1]
    filetype = basefile.split('.')[-1]
    return filetype

def build_lignad(ligandfile, forcefield="gaff2", charge_method="bcc", engine="acpype", clean=True):
    ligandfile = os.path.abspath(ligandfile)
    ligandName = os.path.split(ligandfile)[-1][:-4]+'.TOP'
    if not os.path.exists(ligandName): 
        os.mkdir(ligandName)
    cwd = os.getcwd()
    os.chdir(ligandName)
    paras = {
        'ligandfile': ligandfile,
        'forcefield': forcefield,
        'method': charge_method
    }
    cmd = "acpype -i {ligandfile} -b MOL -a {forcefield} -c {method} -f >acpype.log 2>&1 ".format(**paras)
    RC = os.system(cmd)
    if RC != 0:
        print(cmd)
        raise Exception('ERROR run the acpype. see the %s/acpype.log for details.'%ligandName)
    os.chdir('MOL.acpype')
    moltop = pmd.load_file('MOL_GMX.top')
    molgro = pmd.load_file('MOL_GMX.gro', structure=True)
    os.chdir(cwd)
    if clean:
        shutil.rmtree(ligandName)
    return moltop, molgro

def build_protein(pdbfile, forcefield='amber99sb-ildn'):
    #forcefield = {1:"amber03", 2:"amber94", 3:"amber96", 4:"amber99", 5:"amber99sb", 6:"amber99sb-ildn",
    #        7:"amber99sb-star-ildn-mut", 8:"amber14sb"}
    proteinName = os.path.split(pdbfile)[-1][:-4]+'.TOP'
    if not os.path.exists(proteinName):
        os.mkdir(proteinName)
    pdbfile = os.path.abspath(pdbfile)
    cwd = os.getcwd()
    os.chdir(proteinName)
    paras = {
        'gmx':GMXEXE,
        'pdbfile':pdbfile,
        'outfile': '1-pdb2gmx.pdb',
        'topolfile': 'topol.top',
        'forcefield': forcefield,
    }
    cmd = '{gmx} pdb2gmx -f {pdbfile} -o {outfile} -p {topolfile} -ff {forcefield} -ignh > gromacs.log 2>&1 << EOF \n1\n EOF '.format(**paras)
    RC = os.system(cmd)
    if RC != 0:
        print(cmd)
        raise Exception('ERROR run gmx! see the log file for details %s/gromacs.log'%(proteinName))
    
    engine = GMXEngine()
    boxpdb = engine.gmx_box('1-pdb2gmx.pdb', boxtype='triclinic', boxsize=0.9)
    solpdb = engine.gmx_solvate(boxpdb, 'topol.top', maxsol=2)
    ionspdb = engine.gmx_ions(solpdb, 'topol.top', conc=None, nNA=1, nCL=1, neutral=False)
    #engine.
    protgro = pmd.load_file('1-pdb2gmx.pdb', structure=True)
    #load_position_restraints('topol.top')
    prottop  = pmd.load_file('topol.top')
    os.chdir(cwd)
    shutil.rmtree(proteinName)
    return prottop, protgro

def build_topol(receptor, ligand, outpdb, outtop, proteinforce='amber99sb-ildn', ligandforce='gaff2'):
    if isinstance(receptor, str):
        prottop, protgro = build_protein(receptor, forcefield=proteinforce)
    else:
        prottop, protgro = receptor

    if isinstance(ligand, str):
        moltop, molgro = build_lignad(ligand, forcefield=ligandforce)
    else:
        moltop, molgro = ligand

    systop = moltop + prottop
    sysgro = molgro + protgro
    systop.write(outtop)
    sysgro.write_pdb(outpdb)
    lines = []
    CF = 0
    records = ('NA', 'CL')
    with open(outtop) as fr:
        for line in fr:
            if line.startswith('[') and line.split()[1]=='molecules':
                CF = 2
                lines.append('\n; Include Position restraint file\n#ifdef POSRES\n#endif\n')
            if line.startswith(records) and CF:
                tmp = line.strip().split()
                molname, molnum = tmp[0], int(tmp[1])-1
                if molnum > 1:
                    line = '%s        %d\n'%(molname, molnum)
                else:
                    line = ''
                CF -= 1
            lines.append(line)
    with open(outtop, 'w') as fw:
        for line in lines:
            fw.write(line)



class BaseObject(object):
    pass

class GMXEngine(BaseObject):
    paras = {}
    gmxlog = 'gromacs.log'
    def __init__(self) -> None:
        super().__init__()

    def _grompp(self,pdbfile, topfile, name, mdpfile, maxwarn=1):
        """
        This function takes a pdb file, a topology file, a name for the output tpr file, and a mdp file as
            input. 
        It then runs grompp to generate the tpr file. 
        The function returns the name of the tpr file
        
        Args:
          pdbfile: The input PDB file
          topfile: The name of the topology file.
          name: the name of the job
          mdpfile: The name of the input .mdp file.
          maxwarn: . Defaults to 1
        
        Returns:
          The name of the tpr file.
        """
        outtpr = '%s.tpr'%name
        args = {
            'gmx': GMXEXE,
            'inputfile': pdbfile,
            'outputfile': outtpr,
            'topfile': topfile,
            'mdpfile': mdpfile,
            'maxwarn': maxwarn
        }
        cmd = '{gmx} grompp -f {mdpfile} -c {inputfile} -r {inputfile} -o {outputfile} -p {topfile} -maxwarn {maxwarn} '.format(**args)
        RC = os.system(cmd+'>>%s 2>&1 '%self.gmxlog)
        if RC != 0:
            print(cmd)
            raise Exception('ERROR run grompp %s'%pdbfile)
        return outtpr
    
    def _mdrun(self, tprfile, nt=NUM_OF_THREAD):
        """
        The function takes a tpr file and runs md
        
        Args:
          tprfile: The name of the tpr file to run.
          nt: number of threads to use
        
        Returns:
          The name of the output file.
        """
        jobname = tprfile[:-4]
        args = {
            'gmx': GMXEXE,
            'inputfile': tprfile,
            'jobname': jobname,
            'nt': nt,
        }

        cmd = '{gmx} mdrun -v -deffnm {jobname} -nt {nt} '.format(**args)
        RC = os.system(cmd+'>>%s 2>&1 '%self.gmxlog)
        if RC != 0:
            print(cmd)
            raise Exception('ERROR run mdrun %s'%tprfile)
        return jobname+'.gro'

    def gmx_box(self, pdbfile, boxtype='triclinic', boxsize=0.9):
        """
        Create a box around a pdbfile with the given boxsize and boxtype
        
        Args:
          pdbfile: the input pdb file
          boxtype: The box type.  Options are:. Defaults to cubic
          boxsize: 
        
        Returns:
          The return value is a list of the files that were created.
        """
        outfile = 'box.pdb'
        args = {
            'gmx': GMXEXE,
            'inputfile': pdbfile,
            'outputfile': outfile,
            'boxtype': boxtype,
        }
        cmd = '{gmx} editconf -f {inputfile} -o {outputfile} -bt {boxtype} '.format(**args)
        if isinstance(boxsize, float):
            cmd += '-d %f ' % boxsize
        elif len(boxsize) == 3:
            cmd += '-box %f %f %f'%(boxsize[0], boxsize[1], boxsize[2])
        RC = os.system(cmd+'>>%s 2>&1'%self.gmxlog)
        if RC != 0:
            print(cmd)
            raise Exception('ERROR add a box for the %s'%pdbfile)
        return outfile
    
    def gmx_solvate(self, pdbfile, topfile, watergro='spc216.gro', maxsol=None):
        """
        Solvate the system using the water model specified in the topology file
        
        Args:
          pdbfile: the input pdb file
          topfile: The name of the topology file to be created.
          watergro: the name of the water coordinate file (default is spc216.gro). Defaults to spc216.gro
          maxsol: maximum number of water molecules to add
        
        Returns:
          The name of the solvated system.
        """
        outfile = 'solv.gro'
        args = {
            'gmx': GMXEXE,
            'inputfile': pdbfile,
            'outputfile': outfile,
            'watergro': watergro,
            'topfile': topfile
        }
        cmd = '{gmx} solvate -cp {inputfile} -cs {watergro} -o {outputfile} -p {topfile} '.format(**args)
        if maxsol:
            cmd += '-maxsol %d '%maxsol
        RC = os.system(cmd+'>>%s 2>&1'%self.gmxlog)
        if RC != 0:
            print(cmd)
            raise Exception('ERROR solvate the %s'%pdbfile)
        return outfile

    def gmx_ions(self, pdbfile, topfile, conc=0.15, mdpfile=os.path.join(MDPFILESDIR, 'ions.mdp'), nNA=None, nCL=None, neutral=True):
        """
        The function gmx_ions() is used to add ions to a system.
        
        Args:
          pdbfile: the input pdb file
          topfile: The name of the topology file.
          conc: concentration of ions in Molar
          mdpfile: the name of the mdp file to use for the energy minimization
        
        Returns:
          a tuple with two elements. The first element is the name of the new pdb file, and the second
        element is the name of the new topology file.
        """
        outfile = 'ions.gro'
        outtpr = self._grompp(pdbfile, topfile, 'ions', mdpfile)
        args = {
            'gmx': GMXEXE,
            'inputfile': outtpr,
            'outputfile': outfile,
            'topfile': topfile,
        }
        if neutral:
            cmd = '{gmx} genion -s {inputfile} -o {outputfile} -p {topfile} -neutral '.format(**args)
        else:
            cmd = '{gmx} genion -s {inputfile} -o {outputfile} -p {topfile} '.format(**args)
        if conc:
            cmd += '-conc %f '%conc
        else:
            if nNA:
                cmd += '-np %d '%nNA
            if nCL:
                cmd += '-nn %d '%nCL
        
        RC = os.system(cmd+'>>%s 2>&1 << EOF \nSOL\n EOF '%self.gmxlog)
        if RC != 0:
            print(cmd)
            raise Exception('ERROR for add ions %s'%pdbfile)
        return outfile, topfile

    def gmx_minim(self, pdbfile, topfile, nt=NUM_OF_THREAD, mdpfile=os.path.join(MDPFILESDIR, 'minim.mdp')):
        """
        The gmx_minim function takes a pdb file and a topology file and runs a minimization on the pdb file
        
        Args:
          pdbfile: the input pdb file
          topfile: The name of the topology file.
          nt: number of threads to use. Defaults to 4
          mdpfile: the name of the mdp file to use.
        
        Returns:
          The output file from the MD simulation.
        """
        outtpr = self._grompp(pdbfile, topfile, 'minim', mdpfile)
        outfile = self._mdrun(outtpr, nt)       
        return outfile

    def gmx_nvt(self, pdbfile, topfile, nt=NUM_OF_THREAD, mdpfile=os.path.join(MDPFILESDIR, 'nvt.mdp')):
        """
        1. grompp: generate tpr file from pdb and topology files
        2. mdrun: run the simulation using the tpr file
        
        The function is called by the following function:
        
        Args:
          pdbfile: the input PDB file
          topfile: The topology file for the system.
          nt: number of threads to use. Defaults to 4
          mdpfile: the mdp file to use
        
        Returns:
          The output file from the last function call.
        """
        outtpr = self._grompp(pdbfile, topfile, 'nvt', mdpfile, maxwarn=2)
        outfile = self._mdrun(outtpr, nt)       
        return outfile
    
    def gmx_npt(self, pdbfile, topfile, nt=NUM_OF_THREAD, mdpfile=os.path.join(MDPFILESDIR, 'npt.mdp')):
        """
        This function runs gromacs npt simulation
        
        Args:
          pdbfile: the input PDB file
          topfile: The topology file for the system.
          nt: number of threads to use. Defaults to 4
          mdpfile: the mdp file to use
        
        Returns:
          The output file from the last function call.
        """
        outtpr = self._grompp(pdbfile, topfile, 'npt', mdpfile, maxwarn=2)
        outfile = self._mdrun(outtpr, nt)       
        return outfile
    
    def gmx_md(self, pdbfile, topfile, nt=NUM_OF_THREAD, mdpfile=os.path.join(MDPFILESDIR, 'md.mdp'), nstep=500000):
        """
        This function is used to generate a gromacs trajectory file from a pdb file
        
        Args:
          pdbfile: The input PDB file.
          topfile: The topology file for the system.
          nt: number of threads
          mdpfile: the mdp file for the simulation
          nstep: the number of steps to run. Defaults to 500000, 500000*0.02 fs = 1 ns
        
        Returns:
          The grofile and xtcfile are being returned.
        """
        mdmdpfile = 'md.mdp'
        fr = open(mdpfile)
        with open(mdmdpfile, 'w') as fw:
            for line in fr:
                if line.startswith('nsteps'):
                    line = 'nsteps      =  %d\n' % nstep
                fw.write(line)
        outtpr = self._grompp(pdbfile, topfile, 'md', mdmdpfile, maxwarn=2)
        grofile = self._mdrun(outtpr, nt)
        xtcfile = os.path.abspath('md.xtc')
        return grofile, xtcfile

    def run_to_ions(self, pdbfile, topfile, rundir=None, boxtype='triclinic', boxsize=0.9, maxsol=None, conc=0.15):
        """
        This function runs the following commands:
        
        gmx pdb2gmx -f <pdbfile> -o <pdbfile>_pdb2gmx.pdb -p <pdbfile>_pdb2gmx.top -ff <ff> -water <water>
        -ignh
        gmx editconf -f <pdbfile>_pdb2gmx.pdb -o <pdbfile>_pdb2gmx_box.pdb -d <boxsize> -bt <boxtype>
        gmx solvate -cp <pdbfile>_pdb2gmx_box.pdb -cs spc216.gro -o <pdbfile>_pdb2gmx_box_solvated.pdb -p
        <pdbfile>_pdb2gmx.top
        gmx grompp -f ions.md
        
        Args:
          pdbfile: the input pdb file
          topfile: The name of the topology file to be used.
          rundir: the directory where the simulation will be run
          boxtype: The box type.  Currently supported are "triclinic", "cubic", and "dodecahedron". Defaults
        to triclinic. Defaults to triclinic
          boxsize: the size of the box in nm
          maxsol: maximum number of water molecules to add
          conc: the concentration of ions to add, in mol/L.
        """
        pdbfile = os.path.abspath(pdbfile)
        topfile = os.path.abspath(topfile)
        if rundir is None:
            rundir = os.path.split(pdbfile)[-1][:-4]+'.GMX'
        if not os.path.exists(rundir):
            os.mkdir(rundir)
        shutil.copy(topfile, rundir)
        cwd = os.getcwd()
        os.chdir(rundir)
        topfile = os.path.split(topfile)[-1]
        boxpdb = self.gmx_box(pdbfile, boxtype=boxtype, boxsize=boxsize)
        solvatedpdb = self.gmx_solvate(boxpdb, topfile, watergro='spc216.gro', maxsol=maxsol)
        ionspdb, topfile = self.gmx_ions(solvatedpdb, topfile, conc=conc, mdpfile=os.path.join(MDPFILESDIR, 'ions.mdp'))
        ionspdb, topfile = os.path.abspath(ionspdb), os.path.abspath(topfile)
        os.chdir(cwd)
        return ionspdb, topfile

    def run_to_minim(self, pdbfile, topfile, rundir=None, boxtype='triclinic', boxsize=0.9, maxsol=None, conc=0.15):
        """
        Runs a minimization on a pdb file, and returns the minimized pdb file and topology
        
        Args:
          pdbfile: the input pdb file
          topfile: the topology file for the system
          rundir: the directory to run the simulation in. If None, the current directory is used.
          boxtype: 'triclinic' or 'cubic'. Defaults to triclinic
          boxsize: the size of the box in nm
          maxsol: maximum number of water molecules to add
          conc: concentration of ions to add (in Molar)
        
        Returns:
          The minimised pdb and topology files.
        """
        ionspdb, topfile = self.run_to_ions(pdbfile, topfile, rundir=rundir, boxtype=boxtype, boxsize=boxsize, maxsol=maxsol, conc=conc)
        if rundir is None:
            rundir = os.path.split(pdbfile)[-1][:-4]+'.GMX'
        cwd = os.getcwd()
        os.chdir(rundir)
        minipdb = self.gmx_minim(ionspdb, topfile, mdpfile=os.path.join(MDPFILESDIR, 'minim.mdp'))
        minipdb, topfile = os.path.abspath(minipdb), os.path.abspath(topfile)
        os.chdir(cwd)
        return minipdb, topfile

    def run_to_npt(self, pdbfile, topfile, rundir=None, boxtype='triclinic', boxsize=0.9, maxsol=None, conc=0.15):
        """
        Run a minimization, then an NVT, then an NPT, then return the name of the final PDB file and the
        topology file
        
        Args:
          pdbfile: the input PDB file
          topfile: the topology file
          rundir: the directory to run the simulation in.
          boxtype: The boxtype is the type of box to use.  The box is the box that the system is put into. 
        The box is used to keep the system from drifting away from the desired temperature.  The box is also
        used to keep the system from rotating.  The box is also. Defaults to triclinic
          boxsize: the length of the edge of the box in nm
          maxsol: maximum number of water molecules to add
          conc: concentration of ions in the box, in mol/L
        
        Returns:
          The last two values are returned, but the first one is not used.
        """
        if rundir is None:
            rundir = os.path.split(pdbfile)[-1][:-4]+'.GMX'
        cwd = os.getcwd()
        minipdb, topfile = self.run_to_minim(pdbfile, topfile, rundir=rundir, boxtype=boxtype, boxsize=boxsize, maxsol=maxsol, conc=conc)
        os.chdir(rundir)
        nvtpdb = self.gmx_nvt(minipdb, topfile, mdpfile=os.path.join(MDPFILESDIR, 'nvt.mdp'))
        nptpdb = self.gmx_npt(nvtpdb, topfile, mdpfile=os.path.join(MDPFILESDIR, 'npt.mdp'))
        nptpdb, topfile = os.path.abspath(nptpdb), os.path.abspath(topfile)
        os.chdir(cwd)
        return nptpdb, topfile

    def run_to_md(self, pdbfile, topfile, rundir=None, boxtype='triclinic', boxsize=0.9, maxsol=None, conc=0.15, nstep=500000):
        """
        Runs a short MD simulation in a box of water
        
        Args:
          pdbfile: the input PDB file
          topfile: the topology file
          rundir: the directory to run the simulation in.
          boxtype: 'triclinic' or 'cubic'. Defaults to triclinic
          boxsize: the length of the edge of the box in nm
          maxsol: maximum number of water molecules to add
          conc: concentration of ions in the box, in mol/L
        """
        if rundir is None:
            rundir = os.path.split(pdbfile)[-1][:-4]+'.GMX'
        nptpdb, topfile = self.run_to_npt(pdbfile, topfile, rundir=rundir, boxtype=boxtype, boxsize=boxsize, maxsol=maxsol, conc=conc)
        cwd = os.getcwd()
        os.chdir(rundir)
        mdgro, mdxtc = self.gmx_md(nptpdb, topfile, mdpfile=os.path.join(MDPFILESDIR, 'md.mdp'), nstep=nstep)
        mdgro, mdxtc, topfile = os.path.abspath(mdgro), mdxtc, os.path.abspath(topfile)
        os.chdir(cwd)

        return mdgro, mdxtc, topfile
        

    def clean(self, pdbfile=None, rundir=None):
        """
        If you pass a directory
        to the function, it will delete the directory. If you don't pass a directory,
        it will delete the file
        
        Args:
          pdbfile: the name of the PDB file to be cleaned
          rundir: the directory where the simulation will be run.
        """
        if rundir is not None:
            shutil.rmtree(rundir)
        if pdbfile is not None:
            rundir = os.path.split(pdbfile)[-1][:-4]+'.GMX'
            shutil.rmtree(rundir)

def load_position_restraints(topfile, outfile=None):
    if outfile is None:
        outfile = topfile
    with open(topfile) as fr:
        lines = fr.readlines()
    POSRES = False
    fw = open(outfile, 'w')
    for line in lines:
        if line.strip().endswith('#ifdef POSRES'):
            POSRES = True
        if line.startswith('#include') and POSRES:
            itpfile = line.split()[1].strip().replace('"','')
            fw.write("\n")
            print(itpfile)
            with open(itpfile) as fr:
                for line in fr:
                    if not line.startswith(';'):
                        fw.write(line)
            fw.write("\n")
            POSRES = False
        else:
            fw.write(line)

def main():
    pdbfile, ligandfile = sys.argv[1], sys.argv[2]

    grofile = 'sys.pdb'
    topfile = 'sys.top'

    build_topol(pdbfile, ligandfile, outpdb=grofile, outtop=topfile)

    engine = GMXEngine()
    engine.run_to_md(grofile, topfile)

if __name__  == "__main__":
    main()
