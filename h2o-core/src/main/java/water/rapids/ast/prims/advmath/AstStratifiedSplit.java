package water.rapids.ast.prims.advmath;

import water.*;
import water.fvec.Chunk;
import water.fvec.Frame;
import water.fvec.NewChunk;
import water.fvec.Vec;
import water.rapids.Rapids;
import water.rapids.Env;
import water.rapids.vals.ValFrame;
import water.rapids.ast.AstPrimitive;
import water.rapids.ast.AstRoot;
import water.util.IcedHashMap;
import water.util.IcedLong;
import water.util.VecUtils;

import java.util.*;

import static water.util.RandomUtils.getRNG;

public class AstStratifiedSplit extends AstPrimitive {
  @Override
  public String[] args() {
    return new String[]{"ary", "test_frac", "seed"};
  }

  @Override
  public int nargs() {
    return 1 + 3;
  } // (h2o.random_stratified_split y test_frac seed)

  @Override
  public String str() {
    return "h2o.random_stratified_split";
  }

  @Override
  public ValFrame apply(Env env, Env.StackHelp stk, AstRoot asts[]) {
    Frame origfr = stk.track(asts[1].exec(env)).getFrame();
    Key<Frame> inputFrKey = Key.make();
    Frame fr = origfr.deepCopy(inputFrKey.toString());
    if (fr.numCols() != 1)
      throw new IllegalArgumentException("Must give a single column to stratify against. Got: " + fr.numCols() + " columns.");
    Vec y = fr.anyVec();
    if (!(y.isCategorical() || (y.isNumeric() && y.isInt())))
      throw new IllegalArgumentException("stratification only applies to integer and categorical columns. Got: " + y.get_type_str());
    final double testFrac = asts[2].exec(env).getNum();
    long seed = (long) asts[3].exec(env).getNum();
    seed = seed == -1 ? new Random().nextLong() : seed;
    final long[] classes = new VecUtils.CollectDomain().doAll(y).domain();
    final int nClass = y.isNumeric() ? classes.length : y.domain().length;
    final String[] domains = y.domain();
    final long[] seeds = new long[nClass]; // seed for each regular fold column (one per class)
    for (int i = 0; i < nClass; ++i)
      seeds[i] = getRNG(seed + i).nextLong();
    String[] dom = new String[]{"train", "test"};
    // create frame with all 0s (default is train)
    Key<Frame> k1 = Key.make();
    Vec resVec = Vec.makeCon(0,fr.anyVec().length());
    resVec.setDomain(new String[]{"train","test"});
    Frame result = new Frame(k1, new String[]{"test_train_split"}, new Vec[]{resVec});
    DKV.put(result);
    // create index frame
    //Frame classIdxs = new ClassIdxTask().doAll(nClass,Vec.T_NUM,fr).outputFrame(null,null,null);
    ClassIdxTask finTask = new ClassIdxTask(nClass).doAll(fr);
    HashSet<Integer> totalIdxs = new HashSet<>();
    for (int i = 0; i < finTask._iarray.length; i++) {
        for (int j = 0; j < finTask._iarray[i].size(); j++)
        totalIdxs.add(finTask._iarray[i].get(j));
    }
    System.out.println("finTask total indexes: " + totalIdxs.size() );
    System.out.println("Orig df size: " + fr.anyVec().length());
    // loop through each class
    HashSet<Integer> usedIdxs = new HashSet<>();
    for (int classLabel = 0; classLabel < nClass; classLabel++) {
        System.out.println("Class label: " + classLabel);
        System.out.println("Lenght of vec :" + finTask._iarray[classLabel].size());
        // extract frame with index locations of the minority class
        // calculate target number of this class to go to test
        int tnum = (int) Math.max(Math.round(finTask._iarray[classLabel].size() * testFrac), 1);
        System.out.println("Grab this num of idxs: " + tnum);

        HashSet<Integer> tmpIdxs = new HashSet<>();
        // randomly select the target number of indexes
        int generated = 0;
        int count = 0;
        while (generated < tnum) {
          int i = (int) (getRNG(count+seed).nextDouble() * finTask._iarray[classLabel].size());
          if (tmpIdxs.contains(finTask._iarray[classLabel].get(i))) { count+=1;continue; }
          tmpIdxs.add(finTask._iarray[classLabel].get(i));
          generated += 1;
          count += 1;
        }
        //System.out.println("Idxs found: " + tmpIdxs.size());
        usedIdxs.addAll(tmpIdxs);
        //System.out.println("Idxs incremented to: " + usedIdxs.size());
    }
    //System.out.println("UsedIdxs size: " + usedIdxs.size());
    //System.out.println("Ratio: " + usedIdxs.size() / (float) fr.anyVec().length());
    new ClassAssignMRTask(usedIdxs).doAll(result.anyVec());
    // clean up temp frames
    fr.delete();
    return new ValFrame(result);
  }
  public static class ClassAssignMRTask extends MRTask<AstStratifiedSplit.ClassAssignMRTask> {
     HashSet<Integer> _idx;
     ClassAssignMRTask(HashSet<Integer> idx) {
         _idx = idx;
     }
     @Override
     public void map(Chunk ck) {
       for (int i = 0; i<ck.len(); i++) {
           if (_idx.contains(ck.cidx() + i)) {
               ck.set((int)i,1.0);
           }
       }
     }

  }

    public class ClassIdxTask extends MRTask<AstStratifiedSplit.ClassIdxTask> {
        IntAry[] _iarray;
        int _nclasses;
        ClassIdxTask(int nclasses) {
           _nclasses = nclasses;
        }

        @Override
        public void map(Chunk[] ck) {
            //IntAry[] _iarray;
            _iarray = new IntAry[_nclasses];
            for (int i = 0; i < _nclasses; i++) { _iarray[i] = new IntAry(); }
            for (int i = 0; i < ck[0].len(); i++) {
                try {
                    int clas = (int) ck[0].at8(i);
                    _iarray[clas].add((int) ck[0].start() + i); //should really be long
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        }
        @Override
        public void reduce(AstStratifiedSplit.ClassIdxTask c) {
           HashSet<Integer> mySize = new HashSet<>();
           for (int i = 0; i<_iarray.length;i++){
               for (int j = 0;j<_iarray[i].size();j++) {
                   mySize.add(_iarray[i].get(j));
               }
           }
           System.out.println("Reduce step current size: " + mySize.size());
            mySize = new HashSet<>();
            for (int i = 0; i<c._iarray.length;i++){
                for (int j = 0;j<c._iarray[i].size();j++) {
                    mySize.add(c._iarray[i].get(j));
                }
            }
            System.out.println("Reduce add size: " + mySize.size());
           for (int i = 0; i < c._iarray.length; i++) {
              for (int j = 0; j < c._iarray[i].size(); j++) {
                  _iarray[i].add(c._iarray[i].get(j));
              }
           }
            mySize = new HashSet<>();
            for (int i = 0; i<_iarray.length;i++){
                for (int j = 0;j<_iarray[i].size();j++) {
                    mySize.add(_iarray[i].get(j));
                }
            }
            System.out.println("Reduce step post size: " + mySize.size());
        }
        public class IntAry extends Iced<AstStratifiedSplit.ClassIdxTask.IntAry>  {
            public IntAry(int ...vals){_ary = vals; _sz = vals.length;}
            int [] _ary = new int[4];
            int _sz;
            public void add(int i){
                try {
                    if (_sz == _ary.length)
                        _ary = Arrays.copyOf(_ary, Math.max(4, _ary.length * 2));
                    _ary[_sz++] = i;
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
            public int get(int i){
                if(i >= _sz) throw new ArrayIndexOutOfBoundsException(i);
                return _ary[i];
            }
            public int size(){return _sz;}
            public int[] toArray(){return Arrays.copyOf(_ary,_sz);}

            public void clear() {_sz = 0;}
        }
    }
}
