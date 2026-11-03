import os
import sys
from collections import defaultdict

from cg.api import (
    AreaType,
    Card,
    CardType,
    EnergyType,
    Observation,
    OptionType,
    Pokemon,
    SelectContext,
    all_card_data,
    to_observation_class,
)

"""
Dhelmise / Hide 'n' Sneak Rule-Based Agent
Core Strategy:
- Chooses to go second when winning the opening flip to immediately play Supporters (Explorer's Guidance, Gwynn,
  Lillie's Determination, Naveen) and attack on the first turn.
- Rapidly fuels the discard pile with "Hide 'n' Sneak" Pokémon (Shuppet, Banette, Poltchageist, Sinistcha)
  using Explorer's Guidance, Gwynn, Ultra Ball, Naveen, and Prism Tower.
- Attacks efficiently for 170 damage (1 Psychic Energy) with Dhelmise (Vengeful Anchor) once 4+ Hide 'n' Sneak
  Pokémon are in the discard pile.
- Uses Lillie's Clefairy ex (Fairy Zone + Full Moon Rondo) to turn Dragon-type ex/Mega ex threats into
  Psychic-weak targets (340 damage from Dhelmise) or hit into full benches.
- Spreads 40 damage across the opponent's entire board with Sinistcha (Matcha Spin) once 6+ Hide 'n' Sneak
  Pokémon are in the discard pile, and finishes multi-prize boards with Spiritomb (Spiritual End, 13+ in discard)
  or late-game Bloodmoon Ursaluna ex (Blood Moon for 0-1 Energy).
- Leverages Latias ex (Skyliner) and Poltchageist for free retreat pivoting.
"""

# Card IDs
# Pokémon (25)
Shuppet = 1374              # ×4 (PBL 33) - Basic {P}, 50 HP, Hide 'n' Sneak
Banette = 1375              # ×4 (PBL 34) - Stage 1 {P}, 80 HP, Hide 'n' Sneak, Puppet Pull (80 + search 1)
Poltchageist = 1348         # ×4 (PBL 5)  - Basic {G}, 30 HP, 0 Retreat, Hide 'n' Sneak
Sinistcha = 1349            # ×4 (PBL 6)  - Stage 1 {G}, 60 HP, Hide 'n' Sneak, Matcha Spin (6+ in discard -> 40 to all)
Dhelmise = 1380             # ×4 (PBL 39) - Basic {P}, 140 HP, Vengeful Anchor (4+ in discard -> 170 dmg)
Lillie_Clefairy_ex = 272    # ×2 (JTG 56) - Basic {P} ex, 190 HP, Fairy Zone, Full Moon Rondo
Spiritomb = 1376            # ×1 (PBL 35) - Basic {P}, 60 HP, Spiritual End (13+ in discard -> 4x dmg counters on 2)
Latias_ex = 184             # ×1 (SSP 76) - Basic {P} ex, 210 HP, Skyliner (Basic Pokémon have 0 Retreat)
Bloodmoon_Ursaluna_ex = 44  # ×1 (TWM 141) - Basic {C} ex, 260 HP, Seasoned Skill, Blood Moon (240 dmg)

# Trainers (27)
Explorer_Guidance = 1185     # ×4 (TEF 147) - Look top 6, take 2, discard 4
Lillie_Determination = 1227  # ×3 (MEG 119) - Shuffle hand, draw 6 (or 8 if 6 Prizes)
Gwynn = 1428                 # ×3 (PBL 78)  - Discard up to 2 non-Rule-Box Pokémon, draw 3 per discarded
Boss_Orders = 1182           # ×3 (MEG 114) - Gust opponent Benched Pokémon to Active
Naveen = 1239                # ×2 (POR 79)  - Discard any number from hand, draw until 5 in hand
Ultra_Ball = 1121            # ×4 (MEG 131) - Discard 2 cards, search any Pokémon
Poke_Pad = 1152              # ×3 (POR 81)  - Search non-Rule-Box Pokémon
Night_Stretcher = 1097       # ×3 (ASC 196) - Recover 1 Pokémon or Basic Energy from discard to hand
Prism_Tower = 1430           # ×2 (CRI 80)  - Stadium: Discard 2 cards from hand to draw 1 card

# Energy (8)
Telepath_Psychic_Energy = 19  # ×4 (POR 88 / 87) - Provides {P}; when attached to {P}, bench up to 2 Basic {P}
Basic_Psychic_Energy = 5      # ×3 (MEE 13 / SVE 5) - Basic {P} Energy
Legacy_Energy = 12            # ×1 (TWM 167) - Provides any 1 Energy; reduces Prize taken by 1 when KO'd

HIDE_N_SNEAK_IDS = {Shuppet, Banette, Poltchageist, Sinistcha}
ENERGY_IDS = {Telepath_Psychic_Energy, Basic_Psychic_Energy, Legacy_Energy}

file_path = "deck.csv"
if not os.path.exists(file_path):
    file_path = "/kaggle_simulations/agent/" + file_path

if os.path.exists(file_path):
    with open(file_path, "r") as file:
        csv = [line.strip() for line in file.read().split("\n") if line.strip()]
    my_deck = [int(csv[i]) for i in range(60)]

# Fetch card metadata database and create an ID-to-Card lookup table
all_card = all_card_data()
card_table = {c.cardId: c for c in all_card}


class AttackPlan:
    def __init__(self):
        self.attacker = -1      # 0 = Active, 1..N = Bench index + 1
        self.target = -1        # 0 = Opponent Active, 1..N = Opponent Bench index + 1
        self.attack_index = 0
        self.remain_hp = 9999
        self.energy = False     # True if planned attacker requires manual energy attachment this turn
        self.evolve = False     # True if planned attacker requires evolution this turn


plan = AttackPlan()
pre_turn = -1


def get_card(obs: Observation, area: AreaType, index: int, player_index: int) -> Pokemon | Card | None:
    """Safely extract a Card or Pokemon object from a specific game zone."""
    ps = obs.current.players[player_index]
    match area:
        case AreaType.DECK:
            return obs.select.deck[index] if obs.select.deck and index < len(obs.select.deck) else None
        case AreaType.HAND:
            return ps.hand[index] if ps.hand and index < len(ps.hand) else None
        case AreaType.DISCARD:
            return ps.discard[index] if index < len(ps.discard) else None
        case AreaType.ACTIVE:
            return ps.active[index] if index < len(ps.active) else None
        case AreaType.BENCH:
            return ps.bench[index] if index < len(ps.bench) else None
        case AreaType.PRIZE:
            return ps.prize[index] if index < len(ps.prize) else None
        case AreaType.STADIUM:
            return obs.current.stadium[index] if index < len(obs.current.stadium) else None
        case AreaType.LOOKING:
            return obs.current.looking[index] if obs.current.looking and index < len(obs.current.looking) else None
        case _:
            return None


def prize_count(pokemon: Pokemon) -> int:
    """Calculate how many Prize cards a Pokémon yields upon being Knocked Out."""
    data = card_table[pokemon.id]
    count = 3 if data.megaEx else 2 if data.ex else 1
    for card in pokemon.energyCards:
        if card.id == Legacy_Energy:
            count -= 1
    for card in pokemon.tools:
        if card.id == 1172 and "Lillie" in data.name:  # Lillie's Pearl
            count -= 1
    return max(0, count)


def pokemon_score(pokemon: Pokemon) -> int:
    """Heuristically evaluate the tactical value of targeting an opponent's Pokémon."""
    data = card_table[pokemon.id]
    score = prize_count(pokemon) * 1000
    score += len(pokemon.energies) * 150
    score += len(pokemon.tools) * 100
    if data.stage2:
        score += 250
    elif data.stage1:
        score += 130

    pid = pokemon.id
    # Deprioritize pure setup support ex/basics once used
    if pid in (173, 174, 190, 1071):  # Noctowl, Fan Rotom, Archaludon ex, Meowth ex
        score -= 200
    if pid == 112 and len(pokemon.energies) >= 1:  # Munkidori with energy
        score += 300
    score += pokemon.hp
    return score


def has_psychic_energy(pokemon: Pokemon) -> int:
    """Count how many attached energies can provide Psychic {P} energy."""
    return sum(
        1 for e in pokemon.energies if e in (EnergyType.PSYCHIC, EnergyType.RAINBOW, EnergyType.TEAM_ROCKET)
    )


def effective_damage(
    attacker_id: int,
    base_damage: int,
    op_pokemon: Pokemon,
    clefairy_active: bool,
    stadium_id: int,
) -> int:
    """Compute effective attack damage against op_pokemon factoring in Weakness, Resistance, and Abilities."""
    if base_damage <= 0:
        return 0
    att_data = card_table[attacker_id]
    op_data = card_table[op_pokemon.id]

    # Neutralization Zone (1247): Prevent all damage done to non-Rule-Box Pokémon by opponent's ex/V
    if stadium_id == 1247 and att_data.ex and not op_data.ex:
        return 0

    # Opponent damage-prevention Abilities
    # Crustle (345) / Sylveon (330): Prevent all damage from opponent's Pokémon ex
    if op_pokemon.id in (345, 330) and att_data.ex:
        return 0
    # Farigiraf ex (83): Prevent all damage from opponent's Basic Pokémon ex
    if op_pokemon.id == 83 and att_data.ex and att_data.basic:
        return 0
    # Cornerstone Mask Ogerpon ex (117): Prevent all damage from opponent's Pokémon that have an Ability
    if op_pokemon.id == 117 and len(att_data.skills) > 0:
        return 0

    dmg = base_damage
    att_type = att_data.energyType
    weakness = op_data.weakness
    if clefairy_active and op_data.energyType == EnergyType.DRAGON:
        weakness = EnergyType.PSYCHIC

    if weakness == att_type:
        dmg *= 2
    elif op_data.resistance == att_type:
        dmg = max(0, dmg - 30)

    return dmg


def agent(obs_dict: dict) -> list[int]:
    """Main Rule-Based Agent entry point."""
    obs = to_observation_class(obs_dict)
    if obs.select is None:
        return my_deck

    state = obs.current
    select = obs.select
    context = select.context
    my_index = state.yourIndex
    my_state = state.players[my_index]
    op_state = state.players[1 - my_index]
    my_prize = len(my_state.prize)
    op_prize = len(op_state.prize)
    op_prizes_taken = 6 - op_prize

    global plan
    global pre_turn
    if pre_turn != state.turn:
        pre_turn = state.turn
        plan = AttackPlan()

    field_counts = defaultdict(int)
    hand_counts = defaultdict(int)
    discard_counts = defaultdict(int)

    my_field_pokemon: list[Pokemon] = []
    for card in my_state.active + my_state.bench:
        if card is None:
            continue
        field_counts[card.id] += 1
        my_field_pokemon.append(card)

    if my_state.hand:
        for card in my_state.hand:
            hand_counts[card.id] += 1

    for card in my_state.discard:
        discard_counts[card.id] += 1

    stadium_id = state.stadium[0].id if state.stadium else 0

    # Key deck counts
    hide_n_sneak_discard = sum(discard_counts[cid] for cid in HIDE_N_SNEAK_IDS)
    hide_n_sneak_hand = sum(hand_counts[cid] for cid in HIDE_N_SNEAK_IDS)
    energy_in_hand = sum(hand_counts[cid] for cid in ENERGY_IDS)

    # Check if Iron Thorns ex (37) is in active spot suppressing Rule Box abilities
    op_active_id = op_state.active[0].id if (op_state.active and op_state.active[0] is not None) else 0
    my_active_id = my_state.active[0].id if (my_state.active and my_state.active[0] is not None) else 0
    iron_thorns_lock = (op_active_id == 37 or my_active_id == 37)
    watchtower_lock = (stadium_id == 1256)  # Team Rocket's Watchtower disables {C} Abilities

    latias_active = (field_counts[Latias_ex] >= 1 and not iron_thorns_lock)
    clefairy_active = (field_counts[Lillie_Clefairy_ex] >= 1 and not iron_thorns_lock)

    # Check opponent board properties
    op_cards: list[Pokemon] = []
    if op_state.active and op_state.active[0] is not None:
        op_cards.append(op_state.active[0])
    for pokemon in op_state.bench:
        if pokemon is not None:
            op_cards.append(pokemon)

    op_has_dragon = any(card_table[p.id].energyType == EnergyType.DRAGON for p in op_cards)
    dhelmise_has_energy = any(
        p.id == Dhelmise and has_psychic_energy(p) >= 1 for p in my_field_pokemon
    )

    # Count expendable cards in hand for discard effects (Ultra Ball, Prism Tower, Naveen)
    fodder_count = hide_n_sneak_hand
    if field_counts[Latias_ex] >= 1 and hand_counts[Latias_ex] >= 1:
        fodder_count += hand_counts[Latias_ex]
    if stadium_id == Prism_Tower and hand_counts[Prism_Tower] >= 1:
        fodder_count += hand_counts[Prism_Tower]
    if hide_n_sneak_discard < 9 and hand_counts[Spiritomb] >= 1:
        fodder_count += hand_counts[Spiritomb]

    # Evaluate AttackPlan on every MAIN context step
    can_attack = False
    if context == SelectContext.MAIN:
        plan = AttackPlan()
        can_switch = False
        can_op_switch = False
        evolve_targets = set()

        for o in select.option:
            if o.type == OptionType.PLAY:
                card = get_card(obs, AreaType.HAND, o.index, my_index)
                if card and card.id == Boss_Orders:
                    can_op_switch = True
            elif o.type == OptionType.EVOLVE:
                idx = 0 if o.inPlayArea == AreaType.ACTIVE else (1 + o.inPlayIndex)
                evolve_targets.add(idx)
            elif o.type == OptionType.RETREAT:
                can_switch = True
            elif o.type == OptionType.ATTACK:
                can_attack = True

        # Also note if Latias ex can be played from hand right now to grant free retreat to Basic Active
        if (
            not can_switch
            and not state.retreated
            and my_state.active
            and my_state.active[0] is not None
            and card_table[my_state.active[0].id].basic
            and not (my_state.asleep or my_state.paralyzed)
            and hand_counts[Latias_ex] >= 1
            and len(my_state.bench) < my_state.benchMax
            and not iron_thorns_lock
        ):
            can_switch = True

        my_cards: list[Pokemon] = []
        if my_state.active and my_state.active[0] is not None:
            my_cards.append(my_state.active[0])
        for pokemon in my_state.bench:
            my_cards.append(pokemon)

        can_attach_now = (energy_in_hand >= 1 and not state.energyAttached)

        if state.turn >= 2 and len(my_cards) > 0 and len(op_cards) > 0:
            best_score = -1.0
            for i, my_pokemon in enumerate(my_cards):
                if i != 0 and not can_switch:
                    continue

                pid = my_pokemon.id
                needs_evolve = False
                # Check if Poltchageist or Shuppet can evolve this turn
                if pid == Poltchageist and i in evolve_targets and hide_n_sneak_discard >= 6:
                    pid = Sinistcha
                    needs_evolve = True
                elif pid == Shuppet and i in evolve_targets and hide_n_sneak_discard < 4:
                    pid = Banette
                    needs_evolve = True

                p_energy = has_psychic_energy(my_pokemon)
                t_energy = len(my_pokemon.energies)

                # Determine energy requirements and attack profile
                req_p = 0
                req_t = 0
                base_damage = 0
                base_score = 0.0
                is_spread_matcha = False
                is_spiritual_end = False

                if pid == Dhelmise:
                    req_p = 1
                    req_t = 1
                    base_damage = 170 if hide_n_sneak_discard >= 4 else 30
                    base_score += 260.0  # Favor 1-Prize main attacker
                elif pid == Lillie_Clefairy_ex:
                    req_p = 1
                    req_t = 2
                    total_benched = len(my_state.bench) + len(op_state.bench)
                    base_damage = 20 + 20 * total_benched
                    if my_prize <= 2:
                        base_score -= 180.0
                    else:
                        base_score -= 80.0
                elif pid == Bloodmoon_Ursaluna_ex:
                    req_p = 0
                    req_t = 5 if (iron_thorns_lock or watchtower_lock) else max(0, 5 - op_prizes_taken)
                    base_damage = 240
                    if req_t <= 1:
                        base_score += 180.0
                    else:
                        base_score -= 200.0
                elif pid == Sinistcha:
                    if hide_n_sneak_discard >= 6:
                        req_p = 0
                        req_t = 1
                        is_spread_matcha = True
                        base_score += 200.0
                    else:
                        continue
                elif pid == Spiritomb:
                    if hide_n_sneak_discard >= 13:
                        req_p = 1
                        req_t = 1
                        is_spiritual_end = True
                        base_score += 300.0
                    else:
                        continue
                elif pid == Banette:
                    req_p = 1
                    req_t = 1
                    base_damage = 80
                    base_score += 80.0
                elif pid == Latias_ex:
                    req_p = 2
                    req_t = 3
                    base_damage = 200
                    base_score -= 250.0
                elif pid == Poltchageist:
                    req_p = 0
                    req_t = 1
                    base_damage = 10
                    base_score -= 350.0
                elif pid == Shuppet:
                    req_p = 1
                    req_t = 1
                    base_damage = 10
                    base_score -= 350.0
                else:
                    continue

                # Check if energy requirement is met (now or with 1 manual attachment)
                more_energy = False
                if p_energy < req_p or t_energy < req_t:
                    if can_attach_now and (p_energy + 1 >= req_p) and (t_energy + 1 >= req_t):
                        more_energy = True
                    else:
                        continue

                # If active Pokémon already has energy and doesn't evolve, but cannot attack (e.g. Blood Moon / Eon Blade lock)
                if (
                    i == 0
                    and not more_energy
                    and not needs_evolve
                    and not can_attack
                    and not (my_state.asleep or my_state.paralyzed)
                ):
                    continue

                # Case 1: Sinistcha's Matcha Spin (places 4 damage counters on each opponent Pokémon)
                if is_spread_matcha:
                    spread_score = base_score
                    prizes_taken = 0
                    rem_hp_active = op_cards[0].hp
                    for idx_op, op_p in enumerate(op_cards):
                        if idx_op > 0 and stadium_id == 1264:  # Battle Cage blocks bench counters
                            continue
                        if op_p.id in HIDE_N_SNEAK_IDS:  # Hide 'n' Sneak prevents effects
                            continue
                        p_val = pokemon_score(op_p)
                        if op_p.hp <= 40:
                            prizes_taken += prize_count(op_p)
                            spread_score += p_val + 600.0
                        else:
                            spread_score += p_val * (40.0 / op_p.hp) * 0.9
                            # Bonus for softening up high-HP targets for Dhelmise or Spiritomb
                            if op_p.hp > 170 and op_p.hp - 40 <= 170:
                                spread_score += 350.0
                            elif hide_n_sneak_discard >= 10 and op_p.maxHp >= 160:
                                spread_score += 250.0
                        if idx_op == 0:
                            rem_hp_active = op_p.hp - 40

                    if my_prize <= prizes_taken and prizes_taken > 0:
                        spread_score = 50000.0
                    if i == 0:
                        spread_score += 220.0
                    spread_score += t_energy

                    if spread_score > best_score:
                        best_score = spread_score
                        plan.attacker = i
                        plan.target = 0
                        plan.attack_index = 0
                        plan.remain_hp = rem_hp_active
                        plan.energy = more_energy
                        plan.evolve = needs_evolve
                    continue

                # Case 2: Spiritomb's Spiritual End (quadruples damage counters on 2 opponent Pokémon)
                if is_spiritual_end:
                    target_candidates = []
                    for op_p in op_cards:
                        if op_p.id in HIDE_N_SNEAK_IDS:
                            continue
                        cur_dmg = op_p.maxHp - op_p.hp
                        added_dmg = 3 * cur_dmg
                        if added_dmg <= 0:
                            continue
                        p_val = pokemon_score(op_p)
                        if added_dmg >= op_p.hp:
                            target_candidates.append((p_val + 1200.0, prize_count(op_p)))
                        else:
                            target_candidates.append((p_val * (added_dmg / op_p.hp), 0))
                    if not target_candidates:
                        continue
                    target_candidates.sort(key=lambda x: x[0], reverse=True)
                    chosen = target_candidates[:2]
                    sp_score = base_score + sum(x[0] for x in chosen)
                    prizes_taken = sum(x[1] for x in chosen)
                    if my_prize <= prizes_taken and prizes_taken > 0:
                        sp_score = 50000.0
                    if i == 0:
                        sp_score += 220.0
                    if sp_score > best_score:
                        best_score = sp_score
                        plan.attacker = i
                        plan.target = 0
                        plan.attack_index = 0
                        plan.remain_hp = 0 if prizes_taken > 0 else op_cards[0].hp
                        plan.energy = more_energy
                        plan.evolve = needs_evolve
                    continue

                # Case 3: Standard single-target damaging attacks
                for j, op_pokemon in enumerate(op_cards):
                    if j != 0 and not can_op_switch:
                        break
                    # If we need Boss's Orders (j != 0), ensure we haven't already played a Supporter
                    if j != 0 and state.supporterPlayed:
                        break

                    clefairy_on_field = clefairy_active or (pid == Lillie_Clefairy_ex and not iron_thorns_lock)
                    damage = effective_damage(pid, base_damage, op_pokemon, clefairy_on_field, stadium_id)
                    if damage <= 0:
                        continue

                    prize = 0
                    score = float(pokemon_score(op_pokemon))
                    if op_pokemon.hp <= damage:
                        prize = prize_count(op_pokemon)
                        score += 500.0 * prize
                    else:
                        score *= damage / op_pokemon.hp

                    score += base_score
                    if my_prize <= prize and prize > 0:
                        score = 50000.0

                    if i == 0:
                        score += 220.0
                    if j == 0:
                        score += 350.0
                    score += t_energy

                    if score > best_score:
                        best_score = score
                        plan.attacker = i
                        plan.target = j
                        plan.attack_index = 0
                        plan.remain_hp = op_pokemon.hp - damage
                        plan.energy = more_energy
                        plan.evolve = needs_evolve

    # Helper to score which Pokémon should receive an Energy attachment
    def energy_score(pokemon: Pokemon, is_active: bool, in_play_idx: int) -> int:
        p_cnt = has_psychic_energy(pokemon)
        t_cnt = len(pokemon.energies)
        score = 8000
        if is_active:
            score += 20

        field_pos = 0 if is_active else (1 + in_play_idx)
        if plan.attacker == field_pos and plan.energy:
            score += 3000

        pid = pokemon.id
        if pid == Dhelmise:
            if p_cnt == 0:
                score += 1200 if not dhelmise_has_energy else 600
            else:
                score -= 400
        elif pid == Lillie_Clefairy_ex:
            if t_cnt < 2:
                score += 1100 if op_has_dragon else 500
            else:
                score -= 400
        elif pid == Spiritomb:
            if hide_n_sneak_discard >= 12 and p_cnt == 0:
                score += 1300
            else:
                score -= 500
        elif pid in (Sinistcha, Poltchageist):
            if hide_n_sneak_discard >= 5 and t_cnt == 0:
                score += 550
            else:
                score -= 400
        elif pid == Bloodmoon_Ursaluna_ex:
            req_e = max(0, 5 - op_prizes_taken)
            if 1 <= req_e <= 2 and t_cnt < req_e:
                score += 900
            else:
                score -= 500
        elif pid == Banette:
            if hide_n_sneak_discard < 4 and p_cnt == 0:
                score += 400
            else:
                score -= 400
        elif pid == Latias_ex:
            score -= 600
        elif pid == Shuppet:
            score -= 700
        return score

    # Helper to score discarding a card from hand (higher = more eager to discard)
    def discard_hand_score(card: Card) -> int:
        cid = card.id
        # Top priority: Hide 'n' Sneak Pokémon!
        if cid == Banette:
            return 1000
        if cid == Shuppet:
            return 950
        if cid == Sinistcha:
            # Keep 1 Sinistcha if Poltchageist is on field, >=6 in discard, and we have 1 in hand
            if field_counts[Poltchageist] >= 1 and hide_n_sneak_discard >= 6 and hand_counts[Sinistcha] == 1:
                return 300
            return 900
        if cid == Poltchageist:
            if field_counts[Poltchageist] + field_counts[Sinistcha] == 0 and hand_counts[Poltchageist] == 1:
                return 400
            return 850

        # Duplicate / situational cards that are safe to discard
        if cid == Latias_ex and field_counts[Latias_ex] >= 1:
            return 500
        if cid == Prism_Tower and stadium_id == Prism_Tower:
            return 450
        if cid == Spiritomb and hide_n_sneak_discard < 9:
            return 200
        if cid == Lillie_Clefairy_ex and not op_has_dragon and field_counts[Lillie_Clefairy_ex] >= 1:
            return 150

        # Cards we prefer NOT to discard (negative scores, ordered from least painful to most painful)
        if cid == Poke_Pad:
            return -50
        if cid == Ultra_Ball:
            return -80
        if cid == Prism_Tower:
            return -100
        if cid == Boss_Orders:
            return -120 if hand_counts[Boss_Orders] >= 2 else -250
        if cid == Naveen:
            return -140
        if cid == Lillie_Determination:
            return -160
        if cid == Explorer_Guidance:
            return -180
        if cid == Gwynn:
            return -200
        if cid == Dhelmise:
            return -100 if (field_counts[Dhelmise] >= 2 or hand_counts[Dhelmise] >= 2) else -500
        if cid == Lillie_Clefairy_ex:
            return -400
        if cid == Bloodmoon_Ursaluna_ex:
            return -350 if op_prizes_taken < 3 else -700
        if cid == Spiritomb:
            return -650
        if cid == Latias_ex:
            return -750
        if cid == Night_Stretcher:
            return -450
        if cid == Basic_Psychic_Energy:
            return -600 if energy_in_hand >= 2 else -850
        if cid == Telepath_Psychic_Energy:
            return -700 if energy_in_hand >= 2 else -900
        if cid == Legacy_Energy:
            return -950
        return -200

    # Score every available option
    scores: list[int] = []
    effect_id = select.effect.id if select.effect is not None else 0

    for o in select.option:
        score = 0
        if o.type == OptionType.NUMBER:
            score = o.number if o.number is not None else 0
        elif o.type == OptionType.YES:
            # Prefer going second (OptionType.NO in IS_FIRST) so we can play Supporters & attack on Turn 1!
            score = 0 if context == SelectContext.IS_FIRST else 1
        elif o.type == OptionType.NO:
            score = 1 if context == SelectContext.IS_FIRST else 0
        elif o.type == OptionType.CARD:
            card = get_card(obs, o.area, o.index, o.playerIndex)
            if card is not None:
                energy_count = len(card.energies) if isinstance(card, Pokemon) else 0
                cid = card.id

                if context == SelectContext.SETUP_ACTIVE_POKEMON:
                    # Prefer Poltchageist (0 retreat) or Dhelmise (140 HP 1-Prize attacker) or Latias ex (0 retreat)
                    if cid == Poltchageist:
                        score = 100
                    elif cid == Dhelmise:
                        score = 85
                    elif cid == Latias_ex:
                        score = 75
                    elif cid == Shuppet:
                        score = 55
                    elif cid == Lillie_Clefairy_ex:
                        score = 40
                    elif cid == Spiritomb:
                        score = 30
                    elif cid == Bloodmoon_Ursaluna_ex:
                        score = 10

                elif context == SelectContext.SETUP_BENCH_POKEMON:
                    # Only bench Dhelmise, Latias ex, and at most 1 Poltchageist; keep Hide 'n' Sneak in hand to discard!
                    if cid == Latias_ex:
                        score = 95
                    elif cid == Dhelmise:
                        score = 80
                    elif cid == Poltchageist:
                        score = 40 if field_counts[Poltchageist] == 0 else -50
                    elif cid == Lillie_Clefairy_ex:
                        score = -20
                    elif cid == Bloodmoon_Ursaluna_ex:
                        score = -60
                    elif cid == Spiritomb:
                        score = -80
                    elif cid == Shuppet:
                        score = -100

                elif context in (SelectContext.SWITCH, SelectContext.TO_ACTIVE):
                    if o.playerIndex == my_index:
                        if o.index == plan.attacker - 1:
                            score += 500
                        # If no attacker planned or after a KO, prefer free-retreat pivot or ready attacker
                        if cid == Dhelmise and has_psychic_energy(card) >= 1 and hide_n_sneak_discard >= 4:
                            score += 120
                        elif cid == Poltchageist:
                            score += 110  # 0 retreat pivot!
                        elif cid == Latias_ex and not iron_thorns_lock:
                            score += 105  # 0 retreat pivot!
                        elif cid == Dhelmise:
                            score += 90 + energy_count * 10
                        elif cid == Sinistcha and energy_count >= 1 and hide_n_sneak_discard >= 6:
                            score += 95
                        elif cid == Spiritomb and has_psychic_energy(card) >= 1 and hide_n_sneak_discard >= 13:
                            score += 130
                        elif cid == Bloodmoon_Ursaluna_ex and energy_count >= max(0, 5 - op_prizes_taken):
                            score += 115
                        elif cid == Lillie_Clefairy_ex and energy_count >= 2:
                            score += 85
                        elif cid == Shuppet:
                            score += 40
                        else:
                            score += 20 + energy_count * 5
                    else:
                        # Choosing opponent's Benched Pokémon for Boss's Orders
                        if o.index == plan.target - 1:
                            score += 1000
                        elif isinstance(card, Pokemon):
                            score += pokemon_score(card) // 10

                elif context == SelectContext.TO_BENCH:
                    # e.g., Telepath Psychic Energy searching up to 2 Basic {P} Pokémon to Bench
                    if cid == Latias_ex:
                        score = 300 if (field_counts[Latias_ex] == 0 and hand_counts[Latias_ex] == 0) else -100
                    elif cid == Dhelmise:
                        if field_counts[Dhelmise] == 0:
                            score = 250
                        elif field_counts[Dhelmise] == 1:
                            score = 180
                        elif field_counts[Dhelmise] == 2:
                            score = 50
                        else:
                            score = -50
                    elif cid == Lillie_Clefairy_ex:
                        if op_has_dragon and field_counts[Lillie_Clefairy_ex] == 0:
                            score = 280
                        elif field_counts[Lillie_Clefairy_ex] == 0 and len(op_state.bench) >= 4:
                            score = 120
                        else:
                            score = -40
                    elif cid == Spiritomb:
                        if hide_n_sneak_discard >= 11 and field_counts[Spiritomb] == 0:
                            score = 220
                        else:
                            score = -80
                    elif cid == Shuppet:
                        # Keep Shuppet in deck/discard rather than clogging the Bench!
                        score = -100

                elif context == SelectContext.TO_HAND:
                    # Special handling for Explorer's Guidance (1185): unchosen cards in LOOKING are discarded!
                    if o.area == AreaType.LOOKING or effect_id == Explorer_Guidance:
                        if cid in HIDE_N_SNEAK_IDS:
                            # Leave Hide 'n' Sneak in LOOKING so they get discarded automatically!
                            # Exception: if we urgently need Sinistcha to evolve Poltchageist with >=6 in discard
                            if (
                                cid == Sinistcha
                                and field_counts[Poltchageist] >= 1
                                and hand_counts[Sinistcha] == 0
                                and hide_n_sneak_discard >= 6
                            ):
                                score = 260
                            else:
                                score = -500
                        elif cid in ENERGY_IDS:
                            score = 450 if (energy_in_hand == 0 and not state.energyAttached) else 240
                        elif cid == Dhelmise:
                            score = 420 if (field_counts[Dhelmise] + hand_counts[Dhelmise] < 2) else 180
                        elif cid == Latias_ex:
                            score = 440 if (field_counts[Latias_ex] + hand_counts[Latias_ex] == 0) else 50
                        elif cid == Lillie_Clefairy_ex:
                            score = 430 if (op_has_dragon and field_counts[Lillie_Clefairy_ex] + hand_counts[Lillie_Clefairy_ex] == 0) else 150
                        elif cid == Bloodmoon_Ursaluna_ex:
                            score = 410 if (op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] + hand_counts[Bloodmoon_Ursaluna_ex] == 0) else 140
                        elif cid == Spiritomb:
                            score = 460 if (hide_n_sneak_discard >= 11) else 80
                        elif cid == Gwynn:
                            score = 380 if hide_n_sneak_hand >= 1 else 260
                        elif cid == Explorer_Guidance:
                            score = 340 if hand_counts[Explorer_Guidance] == 0 else 200
                        elif cid == Boss_Orders:
                            score = 350 if hand_counts[Boss_Orders] == 0 else 210
                        elif cid == Ultra_Ball:
                            score = 320
                        elif cid == Night_Stretcher:
                            score = 310
                        elif cid == Poke_Pad:
                            score = 300
                        elif cid == Prism_Tower:
                            score = 290 if stadium_id != Prism_Tower else 120
                        elif cid == Lillie_Determination:
                            score = 270
                        elif cid == Naveen:
                            score = 265
                        else:
                            score = 100

                    elif o.area == AreaType.DISCARD or effect_id == Night_Stretcher:
                        # Night Stretcher (1097): Recover from discard pile
                        if cid == Basic_Psychic_Energy and energy_in_hand == 0 and not state.energyAttached:
                            score = 600
                        elif cid == Spiritomb and hide_n_sneak_discard >= 13 and field_counts[Spiritomb] + hand_counts[Spiritomb] == 0:
                            score = 580
                        elif cid == Bloodmoon_Ursaluna_ex and op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] + hand_counts[Bloodmoon_Ursaluna_ex] == 0:
                            score = 560
                        elif cid == Lillie_Clefairy_ex and op_has_dragon and field_counts[Lillie_Clefairy_ex] + hand_counts[Lillie_Clefairy_ex] == 0:
                            score = 550
                        elif cid == Dhelmise and field_counts[Dhelmise] + hand_counts[Dhelmise] == 0:
                            score = 540
                        elif cid == Latias_ex and field_counts[Latias_ex] + hand_counts[Latias_ex] == 0:
                            score = 520
                        elif cid == Sinistcha and field_counts[Poltchageist] >= 1 and hand_counts[Sinistcha] == 0 and hide_n_sneak_discard >= 7:
                            score = 480
                        elif cid == Dhelmise and field_counts[Dhelmise] + hand_counts[Dhelmise] == 1:
                            score = 400
                        elif cid == Basic_Psychic_Energy:
                            score = 350
                        elif cid in HIDE_N_SNEAK_IDS:
                            # Avoid removing Hide 'n' Sneak from discard unless necessary
                            score = 50
                        else:
                            score = 200

                    else:
                        # Searching deck (Ultra Ball, Poké Pad, Banette's Puppet Pull)
                        has_discard_outlet = (
                            (hand_counts[Gwynn] >= 1 and not state.supporterPlayed)
                            or (hand_counts[Naveen] >= 1 and not state.supporterPlayed)
                            or hand_counts[Ultra_Ball] >= 2
                            or stadium_id == Prism_Tower
                            or hand_counts[Prism_Tower] >= 1
                        )
                        if cid == Latias_ex and field_counts[Latias_ex] + hand_counts[Latias_ex] == 0:
                            score = 520
                        elif cid == Lillie_Clefairy_ex and op_has_dragon and field_counts[Lillie_Clefairy_ex] + hand_counts[Lillie_Clefairy_ex] == 0:
                            score = 550
                        elif cid == Spiritomb and hide_n_sneak_discard >= 13 and field_counts[Spiritomb] + hand_counts[Spiritomb] == 0:
                            score = 580
                        elif cid == Bloodmoon_Ursaluna_ex and op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] + hand_counts[Bloodmoon_Ursaluna_ex] == 0:
                            score = 560
                        elif cid == Dhelmise and field_counts[Dhelmise] + hand_counts[Dhelmise] == 0:
                            score = 540
                        elif cid == Sinistcha and field_counts[Poltchageist] >= 1 and hand_counts[Sinistcha] == 0 and hide_n_sneak_discard >= 6:
                            score = 490
                        elif cid in HIDE_N_SNEAK_IDS and has_discard_outlet and hide_n_sneak_discard < 6:
                            # Grab Banette/Shuppet to immediately discard with Gwynn / Prism Tower / Naveen!
                            if cid == Banette:
                                score = 470
                            elif cid == Shuppet:
                                score = 460
                            elif cid == Sinistcha:
                                score = 450
                            else:
                                score = 440
                        elif cid == Dhelmise and field_counts[Dhelmise] + hand_counts[Dhelmise] < 2:
                            score = 430
                        elif cid == Poltchageist and field_counts[Poltchageist] + field_counts[Sinistcha] == 0 and hand_counts[Poltchageist] == 0:
                            score = 390
                        elif cid in ENERGY_IDS and energy_in_hand == 0 and not state.energyAttached:
                            score = 530
                        elif cid in HIDE_N_SNEAK_IDS:
                            if cid == Banette:
                                score = 360
                            elif cid == Shuppet:
                                score = 350
                            elif cid == Sinistcha:
                                score = 340
                            else:
                                score = 330
                        else:
                            score = 200 - hand_counts[cid] * 50

                elif context == SelectContext.DISCARD:
                    score = discard_hand_score(card)

                elif context == SelectContext.ATTACH_FROM:
                    if isinstance(card, Pokemon):
                        score = energy_score(card, o.area == AreaType.ACTIVE, o.index)

                elif context in (
                    SelectContext.EFFECT_TARGET,
                    SelectContext.DAMAGE_COUNTER,
                    SelectContext.DAMAGE_COUNTER_ANY,
                    SelectContext.DAMAGE,
                ):
                    if o.playerIndex != my_index and isinstance(card, Pokemon):
                        # e.g., Spiritomb's Spiritual End choosing 2 opponent Pokémon to quadruple damage counters
                        cur_dmg = card.maxHp - card.hp
                        added_dmg = 3 * cur_dmg
                        p_val = pokemon_score(card)
                        if cur_dmg > 0 and added_dmg >= card.hp:
                            score = p_val + prize_count(card) * 2000 + added_dmg * 10
                        elif cur_dmg > 0:
                            score = int(p_val * (added_dmg / card.hp)) + added_dmg * 10
                        else:
                            score = p_val // 10

        elif o.type in (OptionType.ENERGY, OptionType.ENERGY_CARD):
            # When paying retreat or discard energy costs, prefer Basic Psychic Energy over Legacy Energy
            pokemon = get_card(obs, o.area, o.index, o.playerIndex)
            if isinstance(pokemon, Pokemon) and o.energyIndex is not None and o.energyIndex < len(pokemon.energyCards):
                ecard = pokemon.energyCards[o.energyIndex]
                if ecard.id == Basic_Psychic_Energy:
                    score = 100
                elif ecard.id == Telepath_Psychic_Energy:
                    score = 80
                elif ecard.id == Legacy_Energy:
                    score = 10
                else:
                    score = 50
            else:
                score = 50

        elif o.type == OptionType.PLAY:
            card = get_card(obs, AreaType.HAND, o.index, my_index)
            if card is not None:
                cid = card.id
                data = card_table[cid]
                if data.cardType == CardType.POKEMON:
                    if cid == Latias_ex:
                        score = 22000 if field_counts[Latias_ex] == 0 else -1
                    elif cid == Dhelmise:
                        if field_counts[Dhelmise] == 0:
                            score = 21000
                        elif field_counts[Dhelmise] == 1:
                            score = 19500
                        elif field_counts[Dhelmise] == 2 and hand_counts[Dhelmise] >= 2:
                            score = 15000
                        else:
                            score = -1
                    elif cid == Lillie_Clefairy_ex:
                        if op_has_dragon and field_counts[Lillie_Clefairy_ex] == 0:
                            score = 21500
                        elif field_counts[Lillie_Clefairy_ex] == 0 and (
                            len(op_state.bench) >= 4 or field_counts[Dhelmise] == 0
                        ):
                            score = 16500
                        else:
                            score = -1
                    elif cid == Poltchageist:
                        if field_counts[Poltchageist] + field_counts[Sinistcha] == 0 and (
                            hide_n_sneak_discard >= 4 or hand_counts[Poltchageist] >= 2
                        ):
                            score = 17500
                        else:
                            score = -1
                    elif cid == Bloodmoon_Ursaluna_ex:
                        if op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] == 0:
                            score = 20500
                        else:
                            score = -1
                    elif cid == Spiritomb:
                        if hide_n_sneak_discard >= 12 and field_counts[Spiritomb] == 0:
                            score = 20800
                        else:
                            score = -1
                    elif cid == Shuppet:
                        # Only bench Shuppet if our bench is completely empty and we have no other basics
                        if len(my_state.bench) == 0 and field_counts[Dhelmise] == 0:
                            score = 14000
                        else:
                            score = -1
                    else:
                        score = -1
                else:
                    # Trainers & Stadiums
                    if cid == Poke_Pad:
                        score = 14500 if my_state.deckCount >= 1 else -1
                    elif cid == Ultra_Ball:
                        need_key_pokemon = (
                            (field_counts[Latias_ex] == 0 and hand_counts[Latias_ex] == 0)
                            or (field_counts[Dhelmise] == 0 and hand_counts[Dhelmise] == 0)
                            or (op_has_dragon and field_counts[Lillie_Clefairy_ex] == 0 and hand_counts[Lillie_Clefairy_ex] == 0)
                            or (op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] == 0 and hand_counts[Bloodmoon_Ursaluna_ex] == 0)
                            or (hide_n_sneak_discard >= 11 and field_counts[Spiritomb] == 0 and hand_counts[Spiritomb] == 0)
                        )
                        if my_state.deckCount == 0:
                            score = -1
                        elif fodder_count >= 2:
                            score = 14000
                        elif fodder_count >= 1 and (need_key_pokemon or hide_n_sneak_discard == 3 or hide_n_sneak_discard == 5):
                            score = 13500
                        elif need_key_pokemon and len(my_state.hand) >= 4:
                            score = 9000
                        else:
                            score = -1
                    elif cid == Night_Stretcher:
                        want_stretcher = (
                            (energy_in_hand == 0 and not state.energyAttached and discard_counts[Basic_Psychic_Energy] >= 1)
                            or (field_counts[Dhelmise] + hand_counts[Dhelmise] == 0 and discard_counts[Dhelmise] >= 1)
                            or (field_counts[Latias_ex] + hand_counts[Latias_ex] == 0 and discard_counts[Latias_ex] >= 1)
                            or (op_has_dragon and field_counts[Lillie_Clefairy_ex] + hand_counts[Lillie_Clefairy_ex] == 0 and discard_counts[Lillie_Clefairy_ex] >= 1)
                            or (op_prizes_taken >= 4 and field_counts[Bloodmoon_Ursaluna_ex] + hand_counts[Bloodmoon_Ursaluna_ex] == 0 and discard_counts[Bloodmoon_Ursaluna_ex] >= 1)
                            or (hide_n_sneak_discard >= 13 and field_counts[Spiritomb] + hand_counts[Spiritomb] == 0 and discard_counts[Spiritomb] >= 1)
                            or (field_counts[Poltchageist] >= 1 and hand_counts[Sinistcha] == 0 and discard_counts[Sinistcha] >= 1 and hide_n_sneak_discard >= 7)
                        )
                        score = 13800 if want_stretcher else -1
                    elif cid == Prism_Tower:
                        score = 12500 if stadium_id != Prism_Tower else -1
                    elif cid == Boss_Orders:
                        if plan.target >= 1 and not plan.energy:
                            score = 9600 if plan.remain_hp <= 0 else 6500
                        elif plan.target >= 1 and plan.energy and energy_in_hand >= 1 and not state.energyAttached:
                            score = 9600 if plan.remain_hp <= 0 else 6500
                        else:
                            score = -1
                    elif cid == Gwynn:
                        if my_state.deckCount <= 3:
                            score = -1
                        elif hide_n_sneak_hand >= 2:
                            score = 9200
                        elif hide_n_sneak_hand == 1:
                            score = 8100
                        else:
                            score = -1
                    elif cid == Explorer_Guidance:
                        if my_state.deckCount <= 6:
                            score = -1
                        else:
                            score = 8800 if hide_n_sneak_discard < 6 else 7900
                    elif cid == Naveen:
                        if my_state.deckCount <= 4:
                            score = -1
                        elif hide_n_sneak_hand >= 1 and (len(my_state.hand) - fodder_count <= 4):
                            score = 8500
                        elif len(my_state.hand) <= 3:
                            score = 7600
                        else:
                            score = -1
                    elif cid == Lillie_Determination:
                        if my_state.deckCount <= 4:
                            score = -1
                        elif my_prize == 6 and hide_n_sneak_hand == 0:
                            score = 8600
                        elif len(my_state.hand) <= 4 and hide_n_sneak_hand == 0:
                            score = 7800
                        elif plan.attacker == -1 and len(my_state.hand) <= 5:
                            score = 7200
                        else:
                            score = -1

        elif o.type == OptionType.ATTACH:
            card = get_card(obs, AreaType.HAND, o.index, my_index)
            pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            if card is not None and pokemon is not None:
                is_active = (o.inPlayArea == AreaType.ACTIVE)
                base_e_score = energy_score(pokemon, is_active, o.inPlayIndex)
                if base_e_score < 7800:
                    score = -1
                else:
                    score = base_e_score
                    # Prefer Telepath Psychic Energy on {P} Pokémon when bench has space
                    p_data = card_table[pokemon.id]
                    if card.id == Telepath_Psychic_Energy:
                        if p_data.energyType == EnergyType.PSYCHIC and len(my_state.bench) < my_state.benchMax:
                            score += 120
                        else:
                            score -= 20
                    elif card.id == Legacy_Energy:
                        if pokemon.id in (Lillie_Clefairy_ex, Bloodmoon_Ursaluna_ex) or (
                            pokemon.id == Dhelmise and is_active
                        ):
                            score += 80
                        else:
                            score -= 40
                    # Lock in energy before Lillie's Determination shuffles the hand
                    if score >= 8500:
                        score += 2000

        elif o.type == OptionType.EVOLVE:
            pokemon = get_card(obs, o.inPlayArea, o.inPlayIndex, my_index)
            evolve_card = get_card(obs, o.area, o.index, my_index)
            if pokemon is not None and evolve_card is not None:
                field_pos = 0 if o.inPlayArea == AreaType.ACTIVE else (1 + o.inPlayIndex)
                if plan.attacker == field_pos and plan.evolve:
                    score = 15000
                elif evolve_card.id == Sinistcha and hide_n_sneak_discard >= 6 and hand_counts[Sinistcha] >= 2:
                    score = 7000
                elif evolve_card.id == Banette and hide_n_sneak_discard < 4 and field_pos == 0 and has_psychic_energy(pokemon) >= 1:
                    score = 7500
                else:
                    # Keep Sinistcha and Banette in hand to discard for Hide 'n' Sneak!
                    score = -1

        elif o.type == OptionType.ABILITY:
            card = get_card(obs, o.area, o.index, my_index)
            if card is not None:
                if card.id == Prism_Tower:
                    if my_state.deckCount <= 2:
                        score = -1
                    elif fodder_count >= 2:
                        score = 25000
                    elif fodder_count == 1 and len(my_state.hand) >= 5 and hide_n_sneak_discard in (3, 5, 12):
                        score = 16000
                    else:
                        score = -1
                elif card.id == 1267:  # Lumiose City ends turn
                    score = 1 if not can_attack else -1
                else:
                    score = 24000

        elif o.type == OptionType.RETREAT:
            if plan.attacker >= 1:
                score = 5000
            elif (
                plan.attacker == -1
                and my_state.active
                and my_state.active[0] is not None
                and card_table[my_state.active[0].id].ex
                and latias_active
                and any(p.id == Poltchageist for p in my_state.bench)
            ):
                # Free retreat out of a 2-Prize ex into Poltchageist when unable to attack
                score = 2000
            else:
                score = -1

        elif o.type == OptionType.ATTACK:
            score = 1000

        elif o.type == OptionType.END:
            score = 0

        scores.append(score)

    desc_indices = [i for i, _ in sorted(enumerate(scores), key=lambda x: x[1], reverse=True)]

    # For optional multi-select contexts (DISCARD, SETUP_BENCH_POKEMON, TO_BENCH),
    # only pick options with score >= 0 while respecting [minCount, maxCount].
    if context in (SelectContext.DISCARD, SelectContext.SETUP_BENCH_POKEMON, SelectContext.TO_BENCH):
        positive_indices = [i for i in desc_indices if scores[i] >= 0][: select.maxCount]
        if len(positive_indices) < select.minCount:
            return desc_indices[: select.minCount]
        return positive_indices

    return desc_indices[: select.maxCount]
