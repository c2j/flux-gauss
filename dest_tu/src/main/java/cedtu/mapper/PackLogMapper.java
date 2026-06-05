package cedtu.mapper;

import org.apache.ibatis.annotations.*;

@Mapper
public interface PackLogMapper {

    // demo-project/sql/PACK_LOG.sql:108 — BIGFUND.PACK_LOG.log
    int insertLog(@Param("inProcname") String inProcname, @Param("inStepno") String inStepno, @Param("inInfo") String inInfo, @Param("inLevel") String inLevel, @Param("inSqltxt") String inSqltxt, @Param("inSqlparam") String inSqlparam, @Param("vCallstack") String vCallstack, @Param("vErrstack") String vErrstack);
    // demo-project/sql/PACK_LOG.sql:170 — BIGFUND.PACK_LOG.LOG_NOAUTOTRANS
    int insertLogNoautotrans(@Param("inProcname") String inProcname, @Param("inStepno") String inStepno, @Param("inInfo") String inInfo, @Param("inLevel") String inLevel, @Param("inSqltxt") String inSqltxt, @Param("inSqlparam") String inSqlparam, @Param("vCallstack") String vCallstack, @Param("vErrstack") String vErrstack);
}
