package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.BOutCursorMapper;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: b-out-cursor.sql
public class BOutCursorService {
    private static final Logger log = LoggerFactory.getLogger(BOutCursorService.class);

    private final BOutCursorMapper bOutCursorMapper;

    public BOutCursorService(BOutCursorMapper bOutCursorMapper) {
        this.bOutCursorMapper = bOutCursorMapper;
    }

    // Source: PKG_B_OUT_CURSOR.prc_out_cursor (PROCEDURE) — b-out-cursor.sql:7-14
    public List<Map<String, Object>> prcOutCursor() {
        List<Map<String, Object>> pResultResult = null;
        int pResultIdx = 0;
        Object p_result = null;
        String __SQLERRM__ = "";
        int __SQLCODE__ = 0;
        try {
            pResultResult = bOutCursorMapper.selectPrcOutCursor();
            if (pResultResult == null) pResultResult = new java.util.ArrayList<>();
            log.info("");
        } catch (BusinessException __e1) { // NO_DATA_FOUND
            __SQLERRM__ = __e1.getMessage();
            __SQLCODE__ = -1;
            // p_result := <value> — refcursor OUT param promoted to List return (issue #136)
        }
        if (pResultResult != null) { return pResultResult; }
        return java.util.Collections.emptyList();
    }
}
