from __future__ import annotations

from dataclasses import dataclass

from .models import PersonalResponse


@dataclass(frozen=True)
class EffectiveArgument:
    order: int
    idea: str
    developpement: str
    exemple: str
    consequence: str


@dataclass(frozen=True)
class EffectiveResponse:
    reformulation: str
    position: str
    position_claire: str
    arguments: tuple[EffectiveArgument, ...]
    nuance: str
    conclusion: str
    is_personal: bool


def effective_response(response, user) -> EffectiveResponse:
    personal = None
    if user is not None and getattr(user, "is_authenticated", False):
        personal = PersonalResponse.objects.filter(
            user=user,
            response=response,
        ).first()

    if personal is None:
        arguments = tuple(
            EffectiveArgument(
                order=argument.order,
                idea=argument.idea,
                developpement=argument.developpement,
                exemple=argument.exemple,
                consequence=argument.consequence,
            )
            for argument in response.arguments.all()
        )
        return EffectiveResponse(
            reformulation=response.reformulation,
            position=response.position,
            position_claire=response.position_claire,
            arguments=arguments,
            nuance=response.nuance,
            conclusion=response.conclusion,
            is_personal=False,
        )

    if not isinstance(personal.arguments, list):
        raise ValueError("Personal response arguments must be a list.")
    arguments = tuple(
        EffectiveArgument(
            order=int(argument["order"]),
            idea=str(argument["idea"]),
            developpement=str(argument["developpement"]),
            exemple=str(argument["exemple"]),
            consequence=str(argument["consequence"]),
        )
        for argument in personal.arguments
    )
    return EffectiveResponse(
        reformulation=personal.reformulation,
        position=personal.position,
        position_claire=personal.position_claire,
        arguments=arguments,
        nuance=personal.nuance,
        conclusion=personal.conclusion,
        is_personal=True,
    )
