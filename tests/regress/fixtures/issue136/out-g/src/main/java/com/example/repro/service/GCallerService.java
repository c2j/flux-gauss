package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.GCallerMapper;
import com.example.repro.service.GTargetService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: g-arity.sql
public class GCallerService {
    private static final Logger log = LoggerFactory.getLogger(GCallerService.class);

    private final GCallerMapper gCallerMapper;
    private final GTargetService gTargetService;

    public GCallerService(GCallerMapper gCallerMapper, GTargetService gTargetService) {
        this.gCallerMapper = gCallerMapper;
        this.gTargetService = gTargetService;
    }

    // Source: PKG_G_CALLER.prc_caller (PROCEDURE) — g-arity.sql:28-32
    public void prcCaller() {
        String vResult = null;
        vResult = String.valueOf(gTargetService.fncWithDefaults("a", "b", ""));
    }
}
