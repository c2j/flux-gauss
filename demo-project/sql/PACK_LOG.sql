CREATE OR REPLACE PACKAGE BIGFUND.PACK_LOG AS
/**************************************************
--�汾�ţ�V1.0.20060207
--���ߣ�  Dingsiwei
--ʱ�䣺  2006-2-7
--����/���޸����ݣ���
--mail��
*************************************************/
/*************************************************
--�����ƣ�PACK_LOG
--�������̡��������ƣ�PROCEDURE LOG
--���ߣ�  Dingsiwei
--ʱ�䣺  2006-2-7
--���ܣ�  PACK_LOG��������¼��̨��־�Ĺ��߰���������Oracle 9i�����ϰ汾
     �ù��߰���3����ͬ�ӿڶ���Ĺ��̿ɹ�����
--�쳣��  ����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION
************************************************/
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
              IN_INFO     IN DB_LOG.INFO%TYPE,
              IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE);
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE);
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
              IN_INFO     IN DB_LOG.INFO%TYPE);
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE, -- �洢������
              IN_STEPNO   IN DB_LOG.STEP_NO%TYPE, -- ������
              IN_INFO     IN DB_LOG.INFO%TYPE, --��־����
              IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE, -- ����
              IN_SQLTXT   IN DB_LOG.SQL_TXT%TYPE, -- sql���
              IN_SQLPARAM IN DB_LOG.SQL_PARAM%TYPE -- sql����
              );
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE, -- �洢������
              IN_STEPNO   IN DB_LOG.STEP_NO%TYPE, -- ������
              IN_INFO     IN DB_LOG.INFO%TYPE, --��־����
              IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE -- ����
              );

PROCEDURE LOG_NOAUTOTRANS(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
                            IN_STEPNO   IN DB_LOG.STEP_NO%TYPE,
                            IN_INFO     IN DB_LOG.INFO%TYPE,
                            IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE,
                            IN_SQLTXT   IN DB_LOG.SQL_TXT%TYPE,
                            IN_SQLPARAM IN DB_LOG.SQL_PARAM%TYPE
                           );

END PACK_LOG;
/
CREATE OR REPLACE PACKAGE BODY BIGFUND.PACK_LOG AS
/**************************************************
--�汾�ţ�V1.0.20060207
--���ߣ�  Dingsiwei
--ʱ�䣺  2006-2-7
--����/���޸����ݣ���
--mail��
*************************************************/
/*************************************************
--�����ƣ�PACK_LOG
--�������̡��������ƣ�PROCEDURE LOG
--���ߣ�  Dingsiwei
--ʱ�䣺  2006-2-7
--���ܣ�  PACK_LOG��������¼��̨��־�Ĺ��߰���������Oracle 9i�����ϰ汾
     �ù��߰���3����ͬ�ӿڶ���Ĺ��̿ɹ�����
--�쳣��  ����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION
************************************************/

 /*
 DEFAULT_LOG_LEVEL  :����û�û��ָ����¼��־�ĵȼ�������øõȼ���ΪĬ�ϵȼ���¼
 LOG_LEVEL_FILTER   :��־��¼�ȼ���ֵ��ֻ�д��ڵ��ڸõȼ�����־�Żᱻ��¼����־����
 LOGGING_EXCEPTION  :����¼��־���̷����쳣ʱ�������׳��Զ�����쳣���ͣ�LOGGING_EXCEPTION
 */
 DEFAULT_LOG_LEVEL  DB_LOG.LOG_LEVEL%TYPE :='2';
 LOG_LEVEL_FILTER   DB_LOG.LOG_LEVEL%TYPE :='3';
 LOGGING_EXCEPTION      EXCEPTION;
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
              IN_INFO     IN DB_LOG.INFO%TYPE,
              IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE) IS
 BEGIN
   IF (IN_LEVEL = '4') THEN
      BIGFUND.PACK_LOG.LOG(IN_PROCNAME,'',IN_INFO,IN_LEVEL,'','');
   ELSIF (in_LEVEL = '5') THEN
      BIGFUND.PACK_LOG.LOG_NOAUTOTRANS(IN_PROCNAME, '' ,IN_INFO, IN_LEVEL,'','');
   END IF;
 EXCEPTION
     WHEN OTHERS THEN
          ROLLBACK;
          RAISE LOGGING_EXCEPTION;
 END ;
 PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE) IS
 BEGIN
   null;
   --BIGFUND.PACK_LOG.LOG(IN_PROCNAME,'', '', DEFAULT_LOG_LEVEL,'','');
   COMMIT;
 EXCEPTION
     WHEN OTHERS THEN
          ROLLBACK;
          RAISE LOGGING_EXCEPTION;
 END;
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
              IN_INFO     IN DB_LOG.INFO%TYPE) IS
BEGIN
  null;
  --BIGFUND.PACK_LOG.LOG(IN_PROCNAME, '' ,IN_INFO, DEFAULT_LOG_LEVEL,'','');
  COMMIT;
 EXCEPTION
     WHEN OTHERS THEN
          ROLLBACK;
          RAISE LOGGING_EXCEPTION;
END;
  PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE, -- �洢������
                IN_STEPNO   IN DB_LOG.STEP_NO%TYPE, -- ������
                IN_INFO     IN DB_LOG.INFO%TYPE, --��־����
                IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE, -- ����
                IN_SQLTXT   IN DB_LOG.SQL_TXT%TYPE, -- sql���
                IN_SQLPARAM IN DB_LOG.SQL_PARAM%TYPE -- sql����
                ) IS
  PRAGMA AUTONOMOUS_TRANSACTION;
  V_ERRSTACK  VARCHAR2(4000):='';
  V_CALLSTACK VARCHAR2(4000):='';
  BEGIN
    IF (IN_LEVEL >= LOG_LEVEL_FILTER) THEN
      --SELECT DBMS_UTILITY.FORMAT_ERROR_STACK INTO V_ERRSTACK FROM sys_dummy;
      --SELECT DBMS_UTILITY.FORMAT_CALL_STACK INTO V_CALLSTACK FROM sys_dummy;
      INSERT INTO DB_LOG
        (ID,
         PROC_NAME,
         INFO,
         LOG_LEVEL,
         TIME_STAMP,
         CALL_STACK,
         ERR_STACK,
         STEP_NO,
         SQL_TXT,
         SQL_PARAM,
         LOG_DATE
         )
      VALUES
        (LPAD(LOG_SEQ.NEXTVAL, 20, '0'),
         IN_PROCNAME,
         IN_INFO,
         IN_LEVEL,
         to_char(now(), 'YYYYMMDDHH24MISS'),
         V_CALLSTACK,
         V_ERRSTACK,
         IN_STEPNO,
         IN_SQLTXT,
         IN_SQLPARAM,
         to_char(now(), 'YYYYMMDD'));
    END IF;
    COMMIT;
 EXCEPTION
     WHEN OTHERS THEN
          ROLLBACK;
          RAISE LOGGING_EXCEPTION;
END;
PROCEDURE LOG(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE, -- �洢������
              IN_STEPNO   IN DB_LOG.STEP_NO%TYPE, -- ������
              IN_INFO     IN DB_LOG.INFO%TYPE, --��־����
              IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE -- ����
              ) IS
PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  BIGFUND.PACK_LOG.LOG(IN_PROCNAME, IN_STEPNO ,IN_INFO, IN_LEVEL,'','');
  COMMIT;
 EXCEPTION
     WHEN OTHERS THEN
          ROLLBACK;
          RAISE LOGGING_EXCEPTION;
END;


 PROCEDURE LOG_NOAUTOTRANS(IN_PROCNAME IN DB_LOG.PROC_NAME%TYPE,
                            IN_STEPNO   IN DB_LOG.STEP_NO%TYPE,
                            IN_INFO     IN DB_LOG.INFO%TYPE,
                            IN_LEVEL    IN DB_LOG.LOG_LEVEL%TYPE,
                            IN_SQLTXT   IN DB_LOG.SQL_TXT%TYPE,
                            IN_SQLPARAM IN DB_LOG.SQL_PARAM%TYPE
                           ) IS
  V_ERRSTACK  VARCHAR2(4000):='';
  V_CALLSTACK VARCHAR2(4000):='';

  BEGIN
         IF (IN_LEVEL = '5') THEN
      --SELECT DBMS_UTILITY.FORMAT_ERROR_STACK INTO V_ERRSTACK from sys_dummy;
      --SELECT DBMS_UTILITY.FORMAT_CALL_STACK INTO V_CALLSTACK from sys_dummy;
      INSERT INTO DB_LOG
        (ID,
         PROC_NAME,
         INFO,
         LOG_LEVEL,
         TIME_STAMP,
         CALL_STACK,
         ERR_STACK,
         STEP_NO,
         SQL_TXT,
         SQL_PARAM,
         LOG_DATE
         )
      VALUES
        (LPAD(LOG_SEQ.NEXTVAL, 20, '0'),
         IN_PROCNAME,
         IN_INFO,
         IN_LEVEL,
         to_char(now(), 'YYYYMMDDHH24MISS'),
         V_CALLSTACK,
         V_ERRSTACK,
         IN_STEPNO,
         IN_SQLTXT,
         IN_SQLPARAM,
         to_char(now(), 'YYYYMMDD'));
    END IF;
    --COMMIT;
    EXCEPTION
      WHEN OTHERS THEN
       null;
  END;

END PACK_LOG;
/
