package com.baishan.interview.repository;

import com.baishan.interview.domain.InterviewSession;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface InterviewSessionRepository extends JpaRepository<InterviewSession, UUID> {

    List<InterviewSession> findByResumeIdOrderByCreatedAtDesc(UUID resumeId);
}
