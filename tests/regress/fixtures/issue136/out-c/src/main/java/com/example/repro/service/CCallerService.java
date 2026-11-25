package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.CCallerMapper;
import com.example.repro.service.CCalleeService;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: c-atomic.sql
public class CCallerService {
    private static final Logger log = LoggerFactory.getLogger(CCallerService.class);

    private final CCallerMapper cCallerMapper;
    private final CCalleeService cCalleeService;

    public CCallerService(CCallerMapper cCallerMapper, CCalleeService cCalleeService) {
        this.cCallerMapper = cCallerMapper;
        this.cCalleeService = cCalleeService;
    }

    // Source: PKG_C_CALLER.prc_caller (PROCEDURE) — c-atomic.sql:40-44
    public void prcCaller(AtomicReference<String> pCode) {
        pCode.set("0");
        cCalleeService.prcLog("proc", "1", "info", "4", String.valueOf(pCode.get()), "");
    }
}
