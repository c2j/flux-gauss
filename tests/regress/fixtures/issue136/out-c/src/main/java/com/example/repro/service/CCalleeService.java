package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.CCalleeMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: c-atomic.sql
public class CCalleeService {
    private static final Logger log = LoggerFactory.getLogger(CCalleeService.class);

    private final CCalleeMapper cCalleeMapper;

    public CCalleeService(CCalleeMapper cCalleeMapper) {
        this.cCalleeMapper = cCalleeMapper;
    }

    // Source: PKG_C_CALLEE.prc_log (PROCEDURE) — c-atomic.sql:15-18
    public void prcLog(String pA, String pB, String pC) {
        // Auto-generated from stored procedure
    }

    // Source: PKG_C_CALLEE.prc_log (PROCEDURE) — c-atomic.sql:20-30
    public void prcLog(String pA, String pB, String pC, String pD, String pE, String pF) {
        // Auto-generated from stored procedure
    }
}
