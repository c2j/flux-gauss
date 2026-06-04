package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper.PackLogMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: PACK_LOG.sql
public class PackLogService {
    private static final Logger log = LoggerFactory.getLogger(PackLogService.class);

    private final PackLogMapper packLogMapper;

    public PackLogService(PackLogMapper packLogMapper) {
        this.packLogMapper = packLogMapper;
    }

    private String defaultLogLevel = "2";
    private String logLevelFilter = "3";
    private String loggingException = null;
    // Source: PACK_LOG.log (PROCEDURE) — PACK_LOG.sql:73-86
    // ************************************************* --�汾�ţ�V1.0.20060207 --���ߣ�  Dingsiwei --ʱ�䣺  2006-2-7 --����/���޸����ݣ��� --mail�� ************************************************
    // ************************************************ --�����ƣ�PACK_LOG --�������̡��������ƣ�PROCEDURE LOG --���ߣ�  Dingsiwei --ʱ�䣺  2006-2-7 --���ܣ�  PACK_LOG��������¼��̨��־�Ĺ��߰���������Oracle 9i�����ϰ汾 �ù��߰���3����ͬ�ӿڶ���Ĺ��̿ɹ����� --�쳣��  ����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION ***********************************************
    // �洢������
    // ������
    // ��־����
    // ����
    // sql���
    // sql����
    // �洢������
    // ������
    // ��־����
    // ����
    // ************************************************* --�汾�ţ�V1.0.20060207 --���ߣ�  Dingsiwei --ʱ�䣺  2006-2-7 --����/���޸����ݣ��� --mail�� ************************************************
    // ************************************************ --�����ƣ�PACK_LOG --�������̡��������ƣ�PROCEDURE LOG --���ߣ�  Dingsiwei --ʱ�䣺  2006-2-7 --���ܣ�  PACK_LOG��������¼��̨��־�Ĺ��߰���������Oracle 9i�����ϰ汾 �ù��߰���3����ͬ�ӿڶ���Ĺ��̿ɹ����� --�쳣��  ����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION ***********************************************
    // DEFAULT_LOG_LEVEL  :����û�û��ָ����¼��־�ĵȼ�������øõȼ���ΪĬ�ϵȼ���¼ LOG_LEVEL_FILTER   :��־��¼�ȼ���ֵ��ֻ�д��ڵ��ڸõȼ�����־�Żᱻ��¼����־���� LOGGING_EXCEPTION  :����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION
    public void log(String inProcname, String inInfo, String inLevel) {
        try {
            if (("4".equals(inLevel))) {
                this.log(inProcname, "", inInfo, inLevel, "", "");
            } else if (("5".equals(inLevel))) {
                this.logNoautotrans(inProcname, "", inInfo, inLevel, "", "");
            }
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:73
            // Rollback
            throw new BusinessException(e.getMessage());
        }
    }

    // Source: PACK_LOG.log (PROCEDURE) — PACK_LOG.sql:87-96
    public void log(String inProcname) {
        try {
            // BIGFUND.PACK_LOG.LOG(IN_PROCNAME,'', '', DEFAULT_LOG_LEVEL,'','');
            // COMMIT — auto-committed by Spring @Transactional boundary
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:87
            // Rollback
            throw new BusinessException(e.getMessage());
        }
    }

    // Source: PACK_LOG.log (PROCEDURE) — PACK_LOG.sql:97-107
    public void log(String inProcname, String inInfo) {
        try {
            // BIGFUND.PACK_LOG.LOG(IN_PROCNAME, '' ,IN_INFO, DEFAULT_LOG_LEVEL,'','');
            // COMMIT — auto-committed by Spring @Transactional boundary
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:97
            // Rollback
            throw new BusinessException(e.getMessage());
        }
    }

    // Source: PACK_LOG.log (PROCEDURE) — PACK_LOG.sql:108-153
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void log(String inProcname, String inStepno, String inInfo, String inLevel, String inSqltxt, String inSqlparam) {
        String vErrstack = "";
        String vCallstack = "";
        int _sqlRowCount = 0;
        try {
            // �洢������
            // ������
            // ��־����
            // ����
            // sql���
            // sql����
            if ((inLevel.compareTo("3") >= 0)) {
                _sqlRowCount = packLogMapper.insertLog(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam, vErrstack, vCallstack);
            }
            // SELECT DBMS_UTILITY.FORMAT_ERROR_STACK INTO V_ERRSTACK FROM sys_dummy;
            // SELECT DBMS_UTILITY.FORMAT_CALL_STACK INTO V_CALLSTACK FROM sys_dummy;
            // COMMIT — auto-committed by @Transactional(propagation = REQUIRES_NEW)
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:108
            // Rollback
            throw new BusinessException(e.getMessage());
        }
    }

    // Source: PACK_LOG.log (PROCEDURE) — PACK_LOG.sql:154-167
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void log(String inProcname, String inStepno, String inInfo, String inLevel) {
        try {
            // �洢������
            // ������
            // ��־����
            // ����
            this.log(inProcname, inStepno, inInfo, inLevel, "", "");
            // COMMIT — auto-committed by @Transactional(propagation = REQUIRES_NEW)
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:154
            // Rollback
            throw new BusinessException(e.getMessage());
        }
    }

    // Source: PACK_LOG.LOG_NOAUTOTRANS (PROCEDURE) — PACK_LOG.sql:170-214
    @Transactional
    public void logNoautotrans(String inProcname, String inStepno, String inInfo, String inLevel, String inSqltxt, String inSqlparam) {
        String vErrstack = "";
        String vCallstack = "";
        int _sqlRowCount = 0;
        try {
            // COMMIT;
            // SELECT DBMS_UTILITY.FORMAT_ERROR_STACK INTO V_ERRSTACK from sys_dummy;
            // SELECT DBMS_UTILITY.FORMAT_CALL_STACK INTO V_CALLSTACK from sys_dummy;
            if (("5".equals(inLevel))) {
                _sqlRowCount = packLogMapper.insertLogNoautotrans(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam, vErrstack, vCallstack);
            }
        } catch (Exception e) { // OTHERS — src: PACK_LOG.sql:170
        }
    }
}
