package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper.PackLogMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.interceptor.TransactionAspectSupport;

@Service
// Source: demo-project/sql/PACK_LOG.sql
public class PackLogService {
    private static final Logger log = LoggerFactory.getLogger(PackLogService.class);

    private final PackLogMapper packLogMapper;
    private static Object defaultLogLevel = "2";
    private static Object logLevelFilter = "3";
    private static String loggingException = null;

    public PackLogService(PackLogMapper packLogMapper) {
            this.packLogMapper = packLogMapper;
    }

        // Source: BIGFUND.PACK_LOG.log (PROCEDURE) — demo-project/sql/PACK_LOG.sql:73-86
        public void log(String inProcname, String inInfo, String inLevel) {
            try {
            if ((java.util.Objects.equals(inLevel, "4"))) {
            // CALL BIGFUND.PACK_LOG.log(inProcname, "", inInfo, inLevel, "", "")
            } else if ((java.util.Objects.equals(inLevel, "5"))) {
            // CALL BIGFUND.PACK_LOG.LOG_NOAUTOTRANS(inProcname, "", inInfo, inLevel, "", "")
            }
            } catch (Exception e) {
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            log.info("");
            }
        }

        // Source: BIGFUND.PACK_LOG.log (PROCEDURE) — demo-project/sql/PACK_LOG.sql:87-96
        public void log(String inProcname) {
            try {
            // COMMIT;
            } catch (Exception e) {
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            log.info("");
            }
        }

        // Source: BIGFUND.PACK_LOG.log (PROCEDURE) — demo-project/sql/PACK_LOG.sql:97-107
        public void log(String inProcname, String inInfo) {
            try {
            // COMMIT;
            } catch (Exception e) {
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            log.info("");
            }
        }

        // Source: BIGFUND.PACK_LOG.log (PROCEDURE) — demo-project/sql/PACK_LOG.sql:108-153
        @Transactional
        public void log(String inProcname, String inStepno, String inInfo, String inLevel, String inSqltxt, String inSqlparam) {
            String vCallstack = "";
            String vErrstack = "";
            try {
            if ((inLevel.compareTo(String.valueOf(logLevelFilter)) >= 0)) {
            packLogMapper.insertLog(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam, vCallstack, vErrstack);
            }
            // COMMIT;
            } catch (Exception e) {
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            log.info("");
            }
        }

        // Source: BIGFUND.PACK_LOG.log (PROCEDURE) — demo-project/sql/PACK_LOG.sql:154-167
        public void log(String inProcname, String inStepno, String inInfo, String inLevel) {
            try {
            // CALL BIGFUND.PACK_LOG.log(inProcname, inStepno, inInfo, inLevel, "", "")
            // COMMIT;
            } catch (Exception e) {
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            log.info("");
            }
        }

        // Source: BIGFUND.PACK_LOG.LOG_NOAUTOTRANS (PROCEDURE) — demo-project/sql/PACK_LOG.sql:170-214
        @Transactional
        public void logNoautotrans(String inProcname, String inStepno, String inInfo, String inLevel, String inSqltxt, String inSqlparam) {
            String vErrstack = "";
            String vCallstack = "";
            try {
            if ((java.util.Objects.equals(inLevel, "5"))) {
            packLogMapper.insertLogNoautotrans(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam, vCallstack, vErrstack);
            }
            } catch (Exception e) {
            }
        }
}
