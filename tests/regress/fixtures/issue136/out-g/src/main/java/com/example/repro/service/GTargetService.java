package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.GTargetMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: g-arity.sql
public class GTargetService {
    private static final Logger log = LoggerFactory.getLogger(GTargetService.class);

    private final GTargetMapper gTargetMapper;

    public GTargetService(GTargetMapper gTargetMapper) {
        this.gTargetMapper = gTargetMapper;
    }

    // Source: PKG_G_TARGET.fnc_with_defaults (FUNCTION) — g-arity.sql:11-18
    public String fncWithDefaults(String pA, String pB, String pC) {
        return String.valueOf(pA);
    }
}
