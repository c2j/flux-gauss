package cedtu.mapper;


import org.apache.ibatis.annotations.*;

@Mapper
public interface PackLogMapper {

        // PACK_LOG.sql:108 — PACK_LOG.log
int insertLog(@Param("inProcname") String inProcname, @Param("inStepno") String inStepno, @Param("inInfo") String inInfo, @Param("inLevel") String inLevel, @Param("inSqltxt") String inSqltxt, @Param("inSqlparam") String inSqlparam, @Param("vErrstack") String vErrstack, @Param("vCallstack") String vCallstack);
// PACK_LOG.sql:170 — PACK_LOG.LOG_NOAUTOTRANS
int insertLogNoautotrans(@Param("inProcname") String inProcname, @Param("inStepno") String inStepno, @Param("inInfo") String inInfo, @Param("inLevel") String inLevel, @Param("inSqltxt") String inSqltxt, @Param("inSqlparam") String inSqlparam, @Param("vErrstack") String vErrstack, @Param("vCallstack") String vCallstack);
}
