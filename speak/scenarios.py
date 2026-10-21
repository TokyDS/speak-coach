"""Сценарии разговора: кем притворяется собеседник и с чего он начинает."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str          # по-русски, для карточки
    blurb: str          # по-русски, одна строка
    role: str           # по-английски, для промпта собеседника
    openers: tuple[str, ...]

    def opener(self, rng: random.Random | None = None) -> str:
        return (rng or random).choice(self.openers)

    def public(self) -> dict:
        return {"key": self.key, "title": self.title, "blurb": self.blurb}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="smalltalk",
        title="Свободный разговор",
        blurb="Как прошёл день, планы, погода, еда: обычная болтовня с другом.",
        role="You are a friendly acquaintance having casual small talk about everyday life: "
             "the learner's day, plans, food, hobbies, weekends.",
        openers=(
            "Hey! Good to see you. How's your week been so far?",
            "Hi there! So, what have you been up to lately?",
            "Hey, how was your weekend? Did you do anything fun?",
        ),
    ),
    Scenario(
        key="kculture",
        title="K-pop, дорамы, кино",
        blurb="Обсудить любимые группы, сериалы и фильмы. Объяснить, почему нравится.",
        role="You are a fellow fan of K-pop, K-dramas and movies. Chat about favourite groups "
             "(for example ILLIT), songs, dramas and films; ask the learner to describe and explain "
             "their opinions, recommend things, and retell plots.",
        openers=(
            "So I heard you're into K-pop. Which group are you listening to the most right now?",
            "Have you watched any good dramas or movies recently? I need something new to watch.",
            "What song have you had on repeat this week?",
        ),
    ),
    Scenario(
        key="job_interview",
        title="Собеседование: .NET-разработчик",
        blurb="Интервьюер спрашивает про опыт, проекты и как ты решаешь задачи.",
        role="You are a friendly hiring manager at an international software company interviewing "
             "the learner for a junior/middle .NET backend developer role. Ask one interview question "
             "at a time: experience, projects, a hard bug they fixed, teamwork, why this job. "
             "Ask natural follow-ups.",
        openers=(
            "Thanks for joining today. To start, could you tell me a little about yourself?",
            "Nice to meet you! Let's begin with your background. How did you get into backend development?",
        ),
    ),
    Scenario(
        key="study_interview",
        title="Интервью в магистратуру в Корее",
        blurb="Комиссия спрашивает о мотивации, учебном плане и почему именно Корея.",
        role="You are a professor on a graduate admissions committee at a Korean university "
             "(scholarship programme interview). Ask about the learner's motivation, study plan, "
             "research interests in computer science, why Korea, and future goals. Be warm but probing.",
        openers=(
            "Welcome, and thank you for coming. Why would you like to do your master's degree in Korea?",
            "Good afternoon. Could you briefly introduce yourself and tell us what you'd like to study?",
        ),
    ),
    Scenario(
        key="seoul_trip",
        title="Путешествие по Сеулу",
        blurb="Кафе, отель, такси, спросить дорогу: бытовые ситуации в поездке.",
        role="You play different people the learner meets while travelling in Seoul: a café barista, "
             "a hotel receptionist, a passer-by giving directions, a shop assistant. Keep each "
             "situation realistic; after a few exchanges, move to a new situation and say who you are now.",
        openers=(
            "Hi, welcome to the hotel! Do you have a reservation with us?",
            "Hello! What can I get for you today?",
        ),
    ),
    Scenario(
        key="standup",
        title="Рабочий созвон",
        blurb="Рассказать, что сделал, над чем работаешь и что мешает.",
        role="You are a team lead at an international company running a daily stand-up and "
             "one-to-one with the learner, a backend developer. Ask what they did, what they are "
             "working on, blockers; ask them to explain technical things in simple words.",
        openers=(
            "Morning! Let's do a quick stand-up. What did you work on yesterday?",
            "Hi! Before we start, could you give me a quick update on your current task?",
        ),
    ),
    Scenario(
        key="debate",
        title="Поспорь со мной",
        blurb="Собеседник занимает позицию, ты возражаешь и аргументируешь.",
        role="You are a playful debate partner. Take a clear position on a light everyday topic "
             "(remote work, social media, learning languages with apps, city vs countryside), invite the "
             "learner to disagree, and push back politely so they must give reasons and examples.",
        openers=(
            "Here's my hot take: working from home is better than working in an office. Agree or disagree?",
            "I think learning languages with apps is a waste of time. Change my mind!",
        ),
    ),
)

_BY_KEY = {s.key: s for s in SCENARIOS}


def get(key: str) -> Scenario:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"неизвестный сценарий: {key}") from None
