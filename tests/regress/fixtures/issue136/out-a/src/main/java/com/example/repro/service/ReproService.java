package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.ReproMapper;
import java.math.BigDecimal;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: a-null-numeric.sql
public class ReproService {
    private static final Logger log = LoggerFactory.getLogger(ReproService.class);

    private final ReproMapper reproMapper;

    public ReproService(ReproMapper reproMapper) {
        this.reproMapper = reproMapper;
    }

    // Source: REPRO.FNC_CALC (FUNCTION) — a-null-numeric.sql:1-9
    public Long fncCalc(long pA, Long pB, long pC) {
        return ((Number)(pA)).longValue();
    }

    // Source: REPRO.PRC_CHECK_NULL (PROCEDURE) — a-caller.sql:1-11
    public void prcCheckNull(long pValue) {
        java.math.BigDecimal vResult = java.math.BigDecimal.ZERO;
        Long vTimeout = 0L;
        vResult = java.math.BigDecimal.valueOf(this.fncCalc(1, null, 0L));
        vTimeout = ((Number)(java.lang.Long.valueOf(pValue))).longValue();
        if (true) {
            vResult = vResult.add(java.math.BigDecimal.valueOf(1));
        }
    }
}
