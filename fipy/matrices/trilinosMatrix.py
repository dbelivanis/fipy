#!/usr/bin/env python

## -*-Pyth-*-
 # ###################################################################
 #  FiPy - Python-based finite volume PDE solver
 # 
 #  FILE: "trilinosMatrix.py"
 #
 #  Author: Jonathan Guyer <guyer@nist.gov>
 #  Author: Daniel Wheeler <daniel.wheeler@nist.gov>
 #  Author: James Warren   <jwarren@nist.gov>
 #  Author: Maxsim Gibiansky <maxsim.gibiansky@nist.gov>
 #    mail: NIST
 #     www: http://www.ctcms.nist.gov/fipy/
 #  
 # ========================================================================
 # This software was developed at the National Institute of Standards
 # and Technology by employees of the Federal Government in the course
 # of their official duties.  Pursuant to title 17 Section 105 of the
 # United States Code this software is not subject to copyright
 # protection and is in the public domain.  FiPy is an experimental
 # system.  NIST assumes no responsibility whatsoever for its use by
 # other parties, and makes no guarantees, expressed or implied, about
 # its quality, reliability, or any other characteristic.  We would
 # appreciate acknowledgement if the software is used.
 # 
 # This software can be redistributed and/or modified freely
 # provided that any derivative works bear some notice that they are
 # derived from it, and any modified versions bear some notice that
 # they have been modified.
 # ========================================================================
 #  
 # ###################################################################
 ##

__docformat__ = 'restructuredtext'

from PyTrilinos import Epetra
from PyTrilinos import EpetraExt

from fipy.matrices.sparseMatrix import _SparseMatrix
from fipy.tools import numerix
from fipy.tools.decorators import getsetDeprecated

# Current inadequacies of the matrix class:

# 1) Adding matrices - the matrix with fewer nonzeros gets added into the one
# that has more; this works as long as it's nonzero entries are a subset of the
# larger one's nonzero entries. Is true for all cases in fipy, but is not true
# in the general case - this isn't a general matrix class like the pysparse
# matrix class is.
#
# 2) addAt currently not guaranteed to work for fill-completed matrices, if
# elements are being added in new spots.
#
# 3) put currently not guaranteed to work for non-empty matrices that do not
# have all the target spots occupied. 
#
# None of these situations currently come up in FiPy; tests do not reveal any of 
# the warnings that guard for those, and all tests pass. Because of the way
# FiPy constructs its matrices, I do not anticipate any of these occurring. 

class _TrilinosMatrixBase(_SparseMatrix):
    """_TrilinosMatrix class wrapper for a PyTrilinos Epetra.CrsMatrix.
    
    Allows basic python operations __add__, __sub__ etc.
    Facilitate matrix populating in an easy way.
    """
    def __init__(self, matrix, bandwidth=None, 
                 nonOverlappingRowMap=None, nonOverlappingColMap=None, 
                 overlappingRowMap=None, overlappingColMap=None):
        """
        :Parameters:
          - `matrix`: The starting `Epetra.CrsMatrix` if there is one.
          - `bandwidth`: The proposed band width of the matrix.
        """
        self.matrix = matrix

        if nonOverlappingRowMap is None:
            self.nonOverlappingRowMap = matrix.RowMap()
        else:
            self.nonOverlappingRowMap = nonOverlappingRowMap

        if nonOverlappingColMap is None:
            self.nonOverlappingColMap = matrix.ColMap()
        else:
            self.nonOverlappingColMap = nonOverlappingColMap

        self.overlappingRowMap = overlappingRowMap
        self.overlappingColMap = overlappingColMap
        
        self.comm = matrix.Comm()
        if bandwidth is None:
            self.bandwidth = ((matrix.NumGlobalNonzeros() + matrix.NumGlobalRows() -1 ) 
                              / matrix.NumGlobalRows())
        else:
            self.bandwidth = bandwidth

    def _setMatrix(self, m):
        self._matrix = m

    matrix = property(lambda self: self._matrix, _setMatrix)

    # All operations that require getting data out of the matrix may need to
    # call FillComplete to make sure they work.  There will be no warnings when
    # FillComplete is implicitly called; there will only be warnings when
    # insertions fail.
    def copy(self):
        if not self.matrix.Filled():
            self.matrix.FillComplete()

        return _TrilinosMatrixBase(matrix=Epetra.CrsMatrix(self.matrix))
            
        
    def __getitem__(self, index):
        if not self.matrix.Filled():
            self.matrix.FillComplete()

        return self.matrix[index]
        
    def __str__(self):

        if not self.matrix.Filled():
            self.matrix.FillComplete()

        from fipy.tools import parallel
        return ''.join(parallel.allgather(_SparseMatrix.__str__(self)))

    @property
    def _range(self):
        return (range(self.nonOverlappingColMap.NumGlobalElements()), self.nonOverlappingColMap.MyGlobalElements())

    def __setitem__(self, index, value):
        self.matrix[index] = value
        

    # Addition is tricky. 
    # Trilinos interface is as such: A can be added into B, but A has to be
    # Filled() beforehand. If B is filled beforehand, this may or may not
    # crash, depending on whether things are being added into spots in B that
    # were not there before.  Have put in some order-of-operands twiddling to
    # make it look like two things can be added in any order.

    # Though not guaranteed to work for arbitrary matrices, it should work for
    # all those generated by FiPy and will give warnings if it encounters
    # trouble (unless Trilinos runs into an error and aborts instead of
    # returning an error code)

    def __iadd__(self, other):
        if other != 0:
            if not other.matrix.Filled():
                other.matrix.FillComplete()
            
            # Depending on which one is more filled, pick the order of operations 
            if self.matrix.Filled() and other.matrix.NumGlobalNonzeros() \
                                            > self.matrix.NumGlobalNonzeros():
                tempBandwidth = other.matrix.NumGlobalNonzeros() \
                                 /self.matrix.NumGlobalRows()+1

                tempMatrix = Epetra.CrsMatrix(Epetra.Copy, self.nonOverlappingRowMap, self.nonOverlappingColMap, tempBandwidth)
                
                if EpetraExt.Add(other.matrix, False, 1, tempMatrix, 1) != 0:
                    import warnings
                    warnings.warn("EpetraExt.Add returned error code in __iadd__, 1",
                                   UserWarning, stacklevel=2)

                if EpetraExt.Add(self.matrix, False, 1, tempMatrix, 1) != 0:
                    import warnings
                    warnings.warn("EpetraExt.Add returned error code in __iadd__, 2",
                                   UserWarning, stacklevel=2)

                self.matrix = tempMatrix
                
            else:
                if EpetraExt.Add(other.matrix, False,1,self.matrix,1) != 0:
                    import warnings
                    warnings.warn("EpetraExt.Add returned error code in __iadd__",
                                   UserWarning, stacklevel=2)

        return self

   
    # To add two things while modifying neither, both must be FillCompleted
    def _add(self, other, sign = 1):

        if not self.matrix.Filled():
            self.matrix.FillComplete()
            
        if not other.matrix.Filled():
            other.matrix.FillComplete()
        
        # make the one with more nonzeros the right-hand operand
        # so addition is likely to succeed
        if self.matrix.NumGlobalNonzeros() > other.matrix.NumGlobalNonzeros():
            tempMatrix = self.copy()
            tempMatrix.__iadd__(other*sign)
        else:
            tempMatrix = other.copy()
            tempMatrix.__iadd__(self*sign)
            
        return tempMatrix

    def __add__(self, other):
        """
        Add two sparse matrices. The nonempty spots of one of them must be a 
        subset of the nonempty spots of the other one.
        
            >>> L = _TrilinosMatrix(rows=3, cols=3)
            >>> L.addAt((3.,10.,numerix.pi,2.5), (0,0,1,2), (2,1,1,0))
            >>> L.addAt([0,0,0], [0,1,2], [0,1,2])
            >>> print L + _TrilinosIdentityMatrix(size=3)
             1.000000  10.000000   3.000000  
                ---     4.141593      ---    
             2.500000      ---     1.000000  
             
            >>> print L + 0
                ---    10.000000   3.000000  
                ---     3.141593      ---    
             2.500000      ---        ---    
            
            >>> print L + 3
            Traceback (most recent call last):
            ...
            AttributeError: 'int' object has no attribute 'matrix'
        """

        if other is 0:
            return self
        else:
            return self._add(other)

    __radd__ = __add__
    
    def __sub__(self, other):
        if other is 0:
            return self
        else:
            return self._add(other, sign=-1)

    def __mul__(self, other):
        """
        Multiply a sparse matrix by another sparse matrix.

            >>> from fipy.tools import parallel
           
            >>> L1 = _TrilinosMatrix(rows=3, cols=3)
            >>> L1.addAt((3,10,numerix.pi,2.5), (0,0,1,2), (2,1,1,0))
            >>> L2 = _TrilinosIdentityMatrix(size=3)
            >>> L2.addAt((4.38,12357.2,1.1), (2,1,0), (1,0,2))
            
            >>> tmp = numerix.array(((1.23572000e+05, 2.31400000e+01, 3.00000000e+00),
            ...                      (3.88212887e+04, 3.14159265e+00, 0.00000000e+00),
            ...                      (2.50000000e+00, 0.00000000e+00, 2.75000000e+00)))

            >>> L = (L1 * L2).numpyArray

            >>> print parallel.Nproc > 1 or numerix.allclose(tmp, L)
            True
            
        or a sparse matrix by a vector

            >>> tmp = numerix.array((29., 6.28318531, 2.5))       
            >>> print parallel.Nproc > 1 or numerix.allclose(L1 * numerix.array((1,2,3),'d'), tmp)
            True
            
        or a vector by a sparse matrix

            >>> tmp = numerix.array((7.5, 16.28318531,  3.))  
            >>> print parallel.Nproc > 1 or numerix.allclose(numerix.array((1,2,3),'d') * L1, tmp) 
            True

            
        """
        N = self.matrix.NumMyCols()

        if isinstance(other, _TrilinosMatrixBase):
            if isinstance(other.matrix, Epetra.RowMatrix):
            
                if not self.matrix.Filled():
                    self.matrix.FillComplete()
                    
                if not other.matrix.Filled():
                    other.matrix.FillComplete()

                result = Epetra.CrsMatrix(Epetra.Copy, self.nonOverlappingRowMap, self.nonOverlappingColMap, 0)

                EpetraExt.Multiply(self.matrix, False, other.matrix, False, result)
                copy = self.copy()
                copy.matrix = result
                return copy
            else:
                raise TypeError
                
        else:
            shape = numerix.shape(other)
            if shape == ():
                result = self.copy()
                result.matrix.Scale(other)
                return result
            elif shape == (N,):

                if not self.matrix.Filled():
                    self.matrix.FillComplete()

                y = _numpyToTrilinosVector(other, self.nonOverlappingColMap)
                result = Epetra.Vector(self.nonOverlappingRowMap)
                self.matrix.Multiply(False, y, result)
                return _trilinosToNumpyVector(result)
            else:
                raise TypeError
           
    def __rmul__(self, other):
        if type(numerix.ones(1)) == type(other):
            y = Epetra.Vector(other)
            result = Epetra.Vector(self.nonOverlappingColMap)
            self.matrix.Multiply(True, y, result)
            return _trilinosToNumpyVector(result)
        else:
            return self * other
            
    @property
    def _shape(self):
        N = self.matrix.NumGlobalRows()
        return (N,N)


        
    def put(self, vector, id1, id2):
        """
        Put elements of `vector` at positions of the matrix corresponding to (`id1`, `id2`)
        
            >>> L = _TrilinosMatrix(rows=3, cols=3)
            >>> L.put((3.,10.,numerix.pi,2.5), (0,0,1,2), (2,1,1,0))
            >>> print L
                ---    10.000000   3.000000  
                ---     3.141593      ---    
             2.500000      ---        ---    
        """

        if hasattr(id1, 'dtype') and id1.dtype.name == 'int64':
            id1 = id1.astype('int32')
        if hasattr(id2, 'dtype') and id2.dtype.name == 'int64':
            id2 = id2.astype('int32')

        if self.matrix.Filled():
            if self.matrix.ReplaceGlobalValues(id1, id2, vector) != 0:
                import warnings
                warnings.warn("ReplaceGlobalValues returned error code in put", 
                               UserWarning, stacklevel=2)
                # Possible different algorithm, to guarantee success:
                # 
                # Make a new matrix, 
                # Use addAt to put the values in it, 
                # Use replaceGlobalValues in the original matrix to zero out the terms 
                # And add the old one into the new one, 
                # Replace the old one.
                #
                # Would incur performance costs, and since FiPy does not use 
                # this function in such a way as would generate these errors,
                # I have not implemented the change.

        else:

            # This guarantees that it will actually replace the values that are there,
            # if there are any
            if self.matrix.NumGlobalNonzeros() == 0:
                self.matrix.InsertGlobalValues(id1, id2, vector)
            else:
                self.matrix.InsertGlobalValues(id1, id2, numerix.zeros(len(vector)))
                if not self.matrix.Filled():
                    self.matrix.FillComplete()
                if self.matrix.ReplaceGlobalValues(id1, id2, vector) != 0:
                    import warnings
                    warnings.warn("ReplaceGlobalValues returned error code in put", 
                                   UserWarning, stacklevel=2)
                    # Possible different algorithm, to guarantee that it does not fail:
                    # 
                    # Make a new matrix, 
                    # Use addAt to put the values in it, 
                    # Use replaceGlobalValues in the original matrix to zero out the terms 
                    # And add the old one into the new one, 
                    # Replace the old one.
                    #
                    # Would incur performance costs, and since FiPy does not use 
                    # this function in such a way as would generate these errors,
                    # I have not implemented the change.
            
                             


    def putDiagonal(self, vector):
        """
        Put elements of `vector` along diagonal of matrix
        
            >>> L = _TrilinosMatrix(rows=3, cols=3)
            >>> L.putDiagonal((3.,10.,numerix.pi))
            >>> print L
             3.000000      ---        ---    
                ---    10.000000      ---    
                ---        ---     3.141593  
            >>> L.putDiagonal((10.,3.))
            >>> print L
            10.000000      ---        ---    
                ---     3.000000      ---    
                ---        ---     3.141593  
        """
        
        
        if type(vector) in [type(1), type(1.)]:
            ids = numerix.arange(self.matrix.NumGlobalRows())
            tmp = numerix.zeros((self.matrix.NumGlobalRows), 'd')
            tmp[:] = vector
            if ids.dtype.name == 'int64':
                ids = ids.astype('int32')
            self.put(tmp, ids, ids)
        else:
            ids = numerix.arange(len(vector))
            if ids.dtype.name == 'int64':
                ids = ids.astype('int32')
            self.put(vector, ids, ids)

    def take(self, id1, id2):
        import warnings
        warnings.warn("""Trying to take from a Trilinos Matrix. That doesn't work.""",
                         UserWarning, stacklevel=2)
        raise TypeError

    def takeDiagonal(self):
        if not self.matrix.Filled():
            self.matrix.FillComplete()

        result = Epetra.Vector(self.nonOverlappingColMap)
        self.matrix.ExtractDiagonalCopy(result)
        
        return result
    
    def addAt(self, vector, id1, id2):
        """
        Add elements of `vector` to the positions in the matrix corresponding to (`id1`,`id2`)
        
            >>> L = _TrilinosMatrix(rows=3, cols=3)
            >>> L.addAt((3.,10.,numerix.pi,2.5), (0,0,1,2), (2,1,1,0))
            >>> L.addAt((1.73,2.2,8.4,3.9,1.23), (1,2,0,0,1), (2,2,0,0,2))
            >>> print L
            12.300000  10.000000   3.000000  
                ---     3.141593   2.960000  
             2.500000      ---     2.200000  
        """

        ## This was added as it seems that trilinos does not like int64 arrays
        if hasattr(id1, 'astype') and id1.dtype.name == 'int64':
            id1 = id1.astype('int32')
        if hasattr(id2, 'astype') and id2.dtype.name == 'int64':
            id2 = id2.astype('int32')

        if not self.matrix.Filled():
            self.matrix.InsertGlobalValues(id1, id2, vector)
        else:
            if self.matrix.SumIntoGlobalValues(id1, id2, vector) != 0:
                import warnings
                warnings.warn("Summing into unfilled matrix returned error code",
                               UserWarning, stacklevel=2)
                # Possible change to this part of the code to do the following:
                #
                # Make a new matrix, 
                # Use addAt to put the values in it
                # Add the old one into the new one
                # Replace the old one. 
                #
                # Would incur performance costs, and since FiPy does not use 
                # this function in such a way as would generate these errors,
                # I have not implemented the change.


    def addAtDiagonal(self, vector):
        if type(vector) in [type(1), type(1.)]:

            if hasattr(self.matrix, 'GetMyRows'):
                Nrows = self.matrix.GetMyRows()
            else:
                Nrows = self.matrix.NumMyRows()
            
            ids = numerix.arange(Nrows)
            tmp = numerix.zeros((Nrows,), 'd')
            tmp[:] = vector
            self.addAt(tmp, ids, ids)
        else:
            ids = numerix.arange(len(vector))
            self.addAt(vector, ids, ids)

    def exportMmf(self, filename):
        """
        Exports the matrix to a Matrix Market file of the given filename.
        """
        if not self.matrix.Filled():
            self.matrix.FillComplete()
        EpetraExt.RowMatrixToMatrixMarketFile(filename, self.matrix)

    @property
    def numpyArray(self):
        import tempfile
        import os
        from scipy.io import mmio
        from fipy.tools import parallel
        
        (f, mtxName) = tempfile.mkstemp(suffix='.mtx')
        mtxName = parallel.bcast(mtxName)

        self.exportMmf(mtxName)

        mtx = mmio.mmread(mtxName)
        parallel.Barrier()
        
        if parallel.procID == 0:
            os.remove(mtxName)

        coo = mtx.tocoo()
        trilinosMatrix = self.matrix
        numpyArray = numerix.zeros((trilinosMatrix.NumGlobalRows(), trilinosMatrix.NumGlobalRows()), 'd')
        numpyArray[coo.row, coo.col] = coo.data
        return numpyArray

    def _getDistributedMatrix(self):
        """
        Returns an equivalent Trilinos matrix, but redistributed evenly over
        all processors.
        """
        if self.comm.NumProc() == 1:
            return self.matrix 
            # No redistribution necessary in serial mode
        else:
##            self._matrix.GlobalAssemble()
            totalElements = self.matrix.NumGlobalRows()

            DistributedMap = Epetra.Map(totalElements, 0, self.comm)
            RootToDist = Epetra.Import(DistributedMap, self.nonOverlappingColMap)

            DistMatrix = Epetra.CrsMatrix(Epetra.Copy, DistributedMap, self.bandwidth*3/2)

            DistMatrix.Import(self.matrix, RootToDist, Epetra.Insert)

            return DistMatrix

    def finalize(self):
        if not self.matrix.Filled():
            self.matrix.FillComplete(self.nonOverlappingColMap, self.nonOverlappingRowMap)
        self.matrix.OptimizeStorage()

def _numpyToTrilinosVector(v, map):
    """
    Takes a numpy vector and return an equivalent Trilinos vector, distributed
    across all processors as specified by the map.
    """
    if(map.Comm().NumProc() == 1):
        return Epetra.Vector(v)
        # No redistribution necessary in serial mode
    else:
        if map.Comm().MyPID() == 0:
            myElements=len(v)
        else:
            myElements=0
        RootMap = Epetra.Map(-1, range(0, myElements), 0, map.Comm())

        RootToDist = Epetra.Import(map, RootMap)

        rootVector = Epetra.Vector(RootMap, v)
        distVector = Epetra.Vector(map)
        distVector.Import(rootVector, RootToDist, Epetra.Insert)
        return distVector

def _trilinosToNumpyVector(v):
    """
    Takes a distributed Trilinos vector and gives all processors a copy of it
    in a numpy vector.
    """

    if(v.Comm().NumProc() == 1):
        return numerix.array(v)
    else:
        PersonalMap = Epetra.Map(-1, range(0, v.GlobalLength()), 0, v.Comm())
        DistToPers = Epetra.Import(PersonalMap, v.Map())

        PersonalV = Epetra.Vector(PersonalMap)
        PersonalV.Import(v, DistToPers, Epetra.Insert) 

        return numerix.array(PersonalV)
        
class _TrilinosMatrix(_TrilinosMatrixBase):
    def __init__(self, rows, cols, bandwidth=1, sizeHint=None, 
                 nonOverlappingRowMap=None, nonOverlappingColMap=None, 
                 overlappingRowMap=None, overlappingColMap=None):
        """Creates a `_TrilinosMatrix`.

        :Parameters:
          - `rows`: The number of matrix rows
          - `cols`: The number of matrix columns
          - `bandwidth`: The proposed band width of the matrix.
          - `sizeHint`: estimate of the number of non-zeros
          - `map`: The Epetra `Map` for the rows that this processor holds
        """
        size = max(rows, cols)
        if sizeHint is not None and bandwidth == 0:
            bandwidth = (sizeHint + size - 1) / (size or 1) 
        else:
            bandwidth = bandwidth
            
        if nonOverlappingRowMap is None:
            comm = Epetra.PyComm()
            # Matrix building gets done on one processor - it gets the map for
            # all the rows
            if comm.MyPID() == 0:
                nonOverlappingRowMap = Epetra.Map(rows, range(0, rows), 0, comm)
            else: 
                nonOverlappingRowMap = Epetra.Map(rows, [], 0, comm)

        if nonOverlappingColMap is None:
            comm = Epetra.PyComm()
            # Matrix building gets done on one processor - it gets the map for
            # all the rows
            if comm.MyPID() == 0:
                nonOverlappingColMap = Epetra.Map(cols, range(0, cols), 0, comm)
            else: 
                nonOverlappingColMap = Epetra.Map(cols, [], 0, comm)

        matrix = Epetra.CrsMatrix(Epetra.Copy, nonOverlappingRowMap, nonOverlappingColMap, bandwidth*3/2)

        # Leave extra bandwidth, to handle multiple insertions into the
        # same spot. It's memory-inefficient, but it'll get cleaned up when
        # FillComplete is called, and according to the Trilinos devs the
        # performance boost will be worth it.
        
        _TrilinosMatrixBase.__init__(self, 
                                     matrix=matrix, 
                                     nonOverlappingRowMap=nonOverlappingRowMap, 
                                     nonOverlappingColMap=nonOverlappingColMap, 
                                     overlappingRowMap=overlappingRowMap, 
                                     overlappingColMap=overlappingColMap, 
                                     bandwidth=bandwidth)

class _TrilinosMeshMatrix(_TrilinosMatrix):
    def __init__(self, mesh, bandwidth=0, sizeHint=None, numberOfVariables=1, numberOfEquations=1):
        """Creates a `_TrilinosMatrix` associated with a `Mesh`

        :Parameters:
          - `mesh`: The `Mesh` to assemble the matrix for.
          - `bandwidth`: The proposed band width of the matrix.
          - `sizeHint`: estimate of the number of non-zeros
          - `numberOfVariables`: The columns of the matrix is determined by numberOfVariables * self.mesh.numberOfCells.
          - `numberOfEquations`: The rows of the matrix is determined by numberOfEquations * self.mesh.numberOfCells.

          
        Tests

        >>> from fipy import *
        >>> matrix = _TrilinosMeshMatrix(mesh=Grid1D(nx=5), numberOfVariables=3, numberOfEquations=3)
        >>> GNO = matrix._globalNonOverlappingRowIDs
        >>> GO = matrix._globalOverlappingRowIDs
        >>> LNO = matrix._localNonOverlappingRowIDs
        >>> print parallel.Nproc != 1 or numerix.allequal(GNO, numerix.arange(15))
        True
        >>> print parallel.Nproc != 1 or numerix.allequal(GO, numerix.arange(15))
        True
        >>> print parallel.Nproc != 1 or numerix.allequal(LNO, numerix.arange(15))
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 1 or numerix.allequal(GNO, [0, 1, 5, 6, 10, 11])
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 1 or numerix.allequal(GO, [0, 1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13])
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 1 or numerix.allequal(LNO, [0, 1, 4, 5, 8, 9])
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 0 or numerix.allequal(GNO, [2, 3, 4, 7, 8, 9, 12, 13, 14])
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 0 or numerix.allequal(GO, numerix.arange(15))
        True
        >>> print parallel.Nproc != 2 or parallel.procID == 0 or numerix.allequal(LNO, [2, 3, 4, 7, 8, 9, 12, 13, 14])
        True

        """
        self.mesh = mesh
        self.numberOfVariables = numberOfVariables
        self.numberOfEquations = numberOfEquations

        comm = mesh.communicator.epetra_comm
        globalNonOverlappingRowIDs = self._globalNonOverlappingRowIDs
        globalNonOverlappingColIDs = self._globalNonOverlappingColIDs
        globalOverlappingRowIDs = self._globalOverlappingRowIDs
        globalOverlappingColIDs = self._globalOverlappingColIDs
        nonOverlappingRowMap = Epetra.Map(-1, list(globalNonOverlappingRowIDs), 0, comm)
        overlappingRowMap = Epetra.Map(-1, list(globalOverlappingRowIDs), 0, comm)
        nonOverlappingColMap = Epetra.Map(-1, list(globalNonOverlappingColIDs), 0, comm)
        overlappingColMap = Epetra.Map(-1, list(globalOverlappingColIDs), 0, comm)

        _TrilinosMatrix.__init__(self, 
                                 rows=self.numberOfEquations * self.mesh.globalNumberOfCells, 
                                 cols=self.numberOfVariables * self.mesh.globalNumberOfCells, 
                                 bandwidth=bandwidth, 
                                 sizeHint=sizeHint, 
                                 nonOverlappingRowMap=nonOverlappingRowMap,
                                 nonOverlappingColMap=nonOverlappingColMap,
                                 overlappingRowMap=overlappingRowMap,
                                 overlappingColMap=overlappingColMap)

    def _cellIDsToGlobalRowIDs(self, IDs):
         N = len(IDs)
         M = self.numberOfEquations
         return (numerix.vstack([IDs] * M) + numerix.indices((M,N))[0] * self.mesh.globalNumberOfCells).flatten()

    def _cellIDsToGlobalColIDs(self, IDs):
         N = len(IDs)
         M = self.numberOfVariables
         return (numerix.vstack([IDs] * M) + numerix.indices((M,N))[0] * self.mesh.globalNumberOfCells).flatten()

    def _cellIDsToLocalRowIDs(self, IDs):
         M = self.numberOfEquations
         N = len(IDs)
         return (numerix.vstack([IDs] * M) + numerix.indices((M,N))[0] * self.mesh.numberOfCells).flatten()

    def _cellIDsToLocalColIDs(self, IDs):
         M = self.numberOfVariables
         N = len(IDs)
         return (numerix.vstack([IDs] * M) + numerix.indices((M,N))[0] * self.mesh.numberOfCells).flatten()

    @getsetDeprecated
    def _getGlobalNonOverlappingRowIDs(self):
        return self._globalNonOverlappingRowIDs

    @property
    def _globalNonOverlappingRowIDs(self):
        return self._cellIDsToGlobalRowIDs(self.mesh._globalNonOverlappingCellIDs)

    @property
    def _globalNonOverlappingColIDs(self):
        return self._cellIDsToGlobalColIDs(self.mesh._globalNonOverlappingCellIDs)

    @getsetDeprecated
    def _getGlobalOverlappingRowIDs(self):
        return self._globalOverlappingRowIDs

    @property
    def _globalOverlappingRowIDs(self):
        return self._cellIDsToGlobalRowIDs(self.mesh._globalOverlappingCellIDs)

    @property
    def _globalOverlappingColIDs(self):
        return self._cellIDsToGlobalColIDs(self.mesh._globalOverlappingCellIDs)

    @getsetDeprecated
    def _getLocalNonOverlappingRowIDs(self):
        return self._localNonOverlappingRowIDs

    @property
    def _localNonOverlappingRowIDs(self):
        return self._cellIDsToLocalRowIDs(self.mesh._localNonOverlappingCellIDs)

    @property
    def _localNonOverlappingColIDs(self):
        return self._cellIDsToLocalColIDs(self.mesh._localNonOverlappingCellIDs)

    def copy(self):
        tmp = _TrilinosMatrix.copy(self)
        copy = self.__class__(mesh=self.mesh, bandwidth=self.bandwidth)
        copy.matrix = tmp._matrix
        return copy
                                 
    def asTrilinosMeshMatrix(self):
        self.finalize()
        return self

    def _getStencil(self, id1, id2):
        globalOverlappingRowIDs = self._globalOverlappingRowIDs
        globalNonOverlappingRowIDs = self._globalNonOverlappingRowIDs
            
        id1 = globalOverlappingRowIDs[id1]
        id2 = globalOverlappingRowIDs[id2]
            
        mask = numerix.in1d(id1, globalNonOverlappingRowIDs) 
        id1 = id1[mask]
        id2 = id2[mask]
        return id1, id2, mask

    def _globalNonOverlapping(self, vector, id1, id2):
        """Transforms and subsets local overlapping values and coordinates to global non-overlapping
        
        :Parameters:
          - `vector`: The overlapping values to insert.
          - `id1`: The local overlapping row indices.
          - `id2`: The local overlapping column indices.
          
        :Returns: 
          Tuple of (non-overlapping vector, 
                    global non-overlapping row indices, 
                    global non-overlapping column indices)
        """
        id1, id2, mask = self._getStencil(id1, id2)
        vector = vector[mask]
        return (vector, id1, id2)

    def flush(self):
        pass
    
    def _getMatrixProperty(self):
        if not hasattr(self, '_matrix'):
            self._matrix = _TrilinosMeshMatrix(self.mesh,
                                               bandwidth=self.bandwidth,
                                               numberOfVariables=self.numberOfVariables,
                                               numberOfEquations=self.numberOfEquations).matrix
        return super(_TrilinosMeshMatrix, self).matrix

    matrix = property(_getMatrixProperty, _TrilinosMatrix._setMatrix)
        
    def put(self, vector, id1, id2):
        vector, id1, id2 = self._globalNonOverlapping(vector, id1, id2)
        _TrilinosMatrix.put(self, vector=vector, id1=id1, id2=id2)

    def addAt(self, vector, id1, id2):
        vector, id1, id2 = self._globalNonOverlapping(vector, id1, id2)
        _TrilinosMatrix.addAt(self, vector=vector, id1=id1, id2=id2)
        
    def takeDiagonal(self):
        nonoverlapping_result = _TrilinosMatrix.takeDiagonal(self)
        
        comm = self.mesh.communicator.epetra_comm
        
        globalOverlappingRowIDs = self._globalOverlappingRowIDs
        overlappingMap = Epetra.Map(-1, list(globalOverlappingRowIDs), 0, comm)

        overlapping_result = Epetra.Vector(overlappingMap)
        overlapping_result.Import(nonoverlapping_result, 
                                  Epetra.Import(overlappingMap, 
                                                self.nonOverlappingColMap), 
                                  Epetra.Insert)

        return overlapping_result

    def __mul__(self, other):
        """
        Multiply a sparse matrix by another sparse matrix.

            >>> from fipy.tools import parallel
           
            >>> L1 = _TrilinosMatrix(rows=3, cols=3)
            >>> L1.addAt((3,10,numerix.pi,2.5), (0,0,1,2), (2,1,1,0))
            >>> L2 = _TrilinosIdentityMatrix(size=3)
            >>> L2.addAt((4.38,12357.2,1.1), (2,1,0), (1,0,2))
            
            >>> tmp = numerix.array(((1.23572000e+05, 2.31400000e+01, 3.00000000e+00),
            ...                      (3.88212887e+04, 3.14159265e+00, 0.00000000e+00),
            ...                      (2.50000000e+00, 0.00000000e+00, 2.75000000e+00)))

            >>> L = (L1 * L2).numpyArray

            >>> print numerix.allclose(tmp, L)
            True
            
        or a sparse matrix by a vector

            >>> tmp = numerix.array((29., 6.28318531, 2.5))       
            >>> print parallel.Nproc > 1 or numerix.allclose(L1 * numerix.array((1,2,3),'d'), tmp)
            True
            
        or a vector by a sparse matrix

            >>> tmp = numerix.array((7.5, 16.28318531,  3.))  
            >>> parallel.Nproc > 1 or numerix.allclose(numerix.array((1,2,3),'d') * L1, tmp) 
            True

        """

        if not self.matrix.Filled():
            self.matrix.FillComplete()

        N = self.matrix.NumMyCols()

        if isinstance(other, _TrilinosMatrixBase):
            return _TrilinosMatrix.__mul__(self, other=other)
        else:
            shape = numerix.shape(other)

            
            if shape == ():
                result = self.copy()
                result.matrix.Scale(other)
                return result
            else:
                if isinstance(other, Epetra.Vector):
                    other_map = other.Map()
                else:
                    other_map = self.overlappingColMap

                if other_map.SameAs(self.overlappingColMap):
                    localNonOverlappingColIDs = self._localNonOverlappingRowIDs

                    other = Epetra.Vector(self.nonOverlappingColMap, 
                                          other[localNonOverlappingColIDs])

                if other.Map().SameAs(self.matrix.ColMap()):

                    nonoverlapping_result = Epetra.Vector(self.nonOverlappingRowMap)

                    self.matrix.Multiply(False, other, nonoverlapping_result)

                    if other_map.SameAs(self.overlappingColMap):
                        overlapping_result = Epetra.Vector(self.overlappingRowMap)
                        overlapping_result.Import(nonoverlapping_result, 
                                                  Epetra.Import(self.overlappingRowMap, 
                                                                self.nonOverlappingRowMap), 
                                                  Epetra.Insert)

                        return overlapping_result
                    else:
                        return nonoverlapping_result
                else:
                    raise TypeError("%s: %s != (%d,)" % (self.__class__, str(shape), N))

class _TrilinosIdentityMatrix(_TrilinosMatrix):
    """
    Represents a sparse identity matrix for Trilinos.
    """
    def __init__(self, size):
        """
        Create a sparse matrix with '1' in the diagonal
        
            >>> print _TrilinosIdentityMatrix(size=3)
             1.000000      ---        ---    
                ---     1.000000      ---    
                ---        ---     1.000000  
        """
        _TrilinosMatrix.__init__(self, rows=size, cols=size, bandwidth=1)
        ids = numerix.arange(size)
        self.addAt(numerix.ones(size), ids, ids)
        
class _TrilinosIdentityMeshMatrix(_TrilinosMeshMatrix):
    def __init__(self, mesh):
        """
        Create a sparse matrix associated with a `Mesh` with '1' in the diagonal
        
            >>> from fipy import Grid1D
            >>> mesh = Grid1D(nx=3)
            >>> print _TrilinosIdentityMeshMatrix(mesh=mesh)
             1.000000      ---        ---    
                ---     1.000000      ---    
                ---        ---     1.000000  
        """
        _TrilinosMeshMatrix.__init__(self, mesh=mesh, bandwidth=1)
        size = mesh.numberOfCells
        ids = numerix.arange(size)
        self.addAt(numerix.ones(size), ids, ids)

class _TrilinosMeshMatrixKeepStencil(_TrilinosMeshMatrix):

    def _getStencil(self, id1, id2):
        if not hasattr(self, 'stencil'):
            self.stencil = _TrilinosMeshMatrix._getStencil(self, id1, id2)

        return self.stencil
    
    def flush(self, cacheStencil=False):
        """Deletes the matrix but maintains the stencil used
        `_globalNonOverlapping()` in as it can be expensive to construct.

        :Parameters:
          - `cacheStencil`: Boolean value to determine whether to keep the stencil (tuple of IDs and a mask) even after deleting the matrix.

        """

        del self._matrix
        if not cacheStencil:
            del self.stencil

def _test(): 
    import doctest
    return doctest.testmod()
    
if __name__ == "__main__": 
    _test() 

