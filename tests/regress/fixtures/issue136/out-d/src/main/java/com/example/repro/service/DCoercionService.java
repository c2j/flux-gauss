package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.DCoercionMapper;
import java.math.BigDecimal;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: d-coercion.sql
public class DCoercionService {
    private static final Logger log = LoggerFactory.getLogger(DCoercionService.class);

    private final DCoercionMapper dCoercionMapper;

    public DCoercionService(DCoercionMapper dCoercionMapper) {
        this.dCoercionMapper = dCoercionMapper;
    }

    // Source: PKG_D_COERCION.fnc_text (FUNCTION) — d-coercion.sql:9-12
    public String fncText(String pValue) {
        return String.valueOf(pValue);
    }

    // Source: PKG_D_COERCION.fnc_decimal (FUNCTION) — d-coercion.sql:14-17
    public Long fncDecimal(String pValue) {
        return (new java.math.BigDecimal(pValue)).longValue();
    }

    // Source: PKG_D_COERCION.prc_assign (PROCEDURE) — d-coercion.sql:19-34
    public void prcAssign(long pNumber) {
        Long vCount = 0L;
        java.math.BigDecimal vDecimal = java.math.BigDecimal.ZERO;
        java.math.BigDecimal vFromNumber = java.math.BigDecimal.ZERO;
        String vText = null;
        vDecimal = new java.math.BigDecimal("1.25");
        vFromNumber = java.math.BigDecimal.valueOf(pNumber);
        vFromNumber = java.math.BigDecimal.valueOf(this.fncDecimal("1"));
        vCount = Long.valueOf(1);
        vCount = Long.parseLong(String.valueOf(this.fncText("1")));
        vDecimal = java.math.BigDecimal.valueOf(vCount).multiply(vDecimal);
        vText = String.valueOf(vCount);
        vText = String.valueOf(this.fncText(String.valueOf(1)));
        vDecimal = /* TOBEFIX: unresolved fn valueOf(vText) - pkg=java, caller=d-coercion.sql:prc_assign */ java.math.BigDecimal.ZERO;
    }
}
