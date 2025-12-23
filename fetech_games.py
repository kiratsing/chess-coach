import berserk
import chess
import chess.pgn
import chess.engine
import io
import requests
# --- ADD THIS IMPORT at the top of your script ---
from collections import defaultdict

# --- CONFIGURATION (unchanged from last step) ---
USERNAME = 'kirat0070' 
NUM_GAMES = 10
STOCKFISH_PATH = '/usr/games/stockfish' 
ANALYSIS_DEPTH = 18 
# ---------------------

def get_mistake_category(cpl):
    """Categorizes the CPL into human-readable terms."""
    if cpl > 300:
        return "Blunder"
    elif cpl > 100:
        return "Mistake"
    elif cpl > 50:
        return "Inaccuracy"
    else:
        return "Good Move"

def get_game_phase(move_num):
    """Determines the phase of the game based on the move number."""
    if move_num <= 10:
        return "Opening"
    elif move_num <= 30:
        return "Middlegame"
    else:
        return "Endgame"
    
# --- (Keep get_centipawn_loss function here - It is unchanged) ---
def get_centipawn_loss(engine, board, move):
    """
    Calculates the CPL for a given move using the best practice of
    comparing the value of the engine's best move to the actual move.
    """
    
    # 1. Evaluate the position *before* the move to find the BEST possible score
    # We use a deeper search here to ensure we find the true best move evaluation
    info_best = engine.analyse(board, chess.engine.Limit(depth=18)) # Use your set ANALYSIS_DEPTH
    score_best = score_to_cp(info_best['score'].relative, mate_value=10000)
    
    # 2. Evaluate the position *after* the user's actual move
    board.push(move)
    info_actual = engine.analyse(board, chess.engine.Limit(depth=18))
    score_actual = score_to_cp(info_actual['score'].relative, mate_value=10000)
    board.pop()
    
    # CPL is the difference between the best possible evaluation and the evaluation after the move.
    # The result is capped at 0 (meaning no negative CPL/no "gain" is possible).
    cpl = score_best - score_actual
    
    return max(0, cpl)

def score_to_cp(score, mate_value=10000):
    """
    Converts a chess.engine.Score object (Cp or Mate) to a raw Centipawn integer.
    Mate scores are mapped to a large, fixed value (mate_value) adjusted for length.
    """
    if score.is_mate():
        # Mate in N moves. Positive for winning, negative for losing.
        mate_in = score.mate()
        if mate_in > 0:
            # Winning mate: (mate_value - N)
            return mate_value - mate_in
        else:
            # Losing mate: -(mate_value + N) where N is a positive number
            return -(mate_value + abs(mate_in))
    else:
        # Standard Centipawn score
        return score.cp

def analyze_game(game_pgn, engine):
    """Processes one game and calculates CPL for every user move, tagging it."""
    pgn_io = io.StringIO(game_pgn)
    game = chess.pgn.read_game(pgn_io)
    board = game.board()
    
    analysis_data = {
        'moves_analyzed': 0,
        'mistakes_by_phase': defaultdict(int), # Stores count of Blunders/Mistakes per phase
        'phase_cpl_sum': defaultdict(lambda: {'cpl': 0, 'count': 0}),
        'user_cpl_values': [] # Stores raw CPL for overall ACPL calculation
    }
    
    # ply() gives the half-move number (1. e4 is ply 1, 1...e5 is ply 2)
    move_count = 0 

    for move in game.mainline_moves():
        move_count += 1
        user_turn = (board.turn == chess.WHITE and game.headers["White"] == USERNAME) or \
                    (board.turn == chess.BLACK and game.headers["Black"] == USERNAME)

        if user_turn:
            cpl = get_centipawn_loss(engine, board, move)
            
            analysis_data['user_cpl_values'].append(cpl)
            analysis_data['moves_analyzed'] += 1
            
            # --- NEW TAGGING LOGIC ---
            category = get_mistake_category(cpl)
            phase = get_game_phase(move_count)
            
            if category != "Good Move":
                # Count the total number of mistakes/blunders in this phase
                analysis_data['mistakes_by_phase'][f"{phase} - {category}"] += 1
            
            # Sum CPL for calculating ACPL per phase
            analysis_data['phase_cpl_sum'][phase]['cpl'] += cpl
            analysis_data['phase_cpl_sum'][phase]['count'] += 1

        board.push(move)
        
    return analysis_data

from collections import defaultdict

def aggregate_all_games(all_results):
    """Combines stats from multiple games into a single summary dictionary."""
    if not all_results:
        return None

    final_summary = {
        'total_user_cpl_values': [],
        'total_moves_analyzed': 0,
        'mistakes_by_phase': defaultdict(int),
        'phase_cpl_sum': defaultdict(lambda: {'cpl': 0, 'count': 0})
    }

    for data in all_results:
        # Combine CPL lists for overall ACPL
        final_summary['total_user_cpl_values'].extend(data['user_cpl_values'])
        final_summary['total_moves_analyzed'] += data['moves_analyzed']

        # Sum up mistake counts across all games
        for error_type, count in data['mistakes_by_phase'].items():
            final_summary['mistakes_by_phase'][error_type] += count
            
        # Sum up CPL and count for ACPL per phase
        for phase, stats in data['phase_cpl_sum'].items():
            final_summary['phase_cpl_sum'][phase]['cpl'] += stats['cpl']
            final_summary['phase_cpl_sum'][phase]['count'] += stats['count']

    return final_summary


def generate_report(aggregated_data):
    """Generates the coaching report based on aggregated analysis."""
    
    if not aggregated_data:
        print("No aggregated data to report.")
        return

    user_cpl_values = aggregated_data['total_user_cpl_values']
    
    if not user_cpl_values:
        print("No moves were analyzed for the user across all games.")
        return

    # 1. Overall Summary
    avg_cpl = sum(user_cpl_values) / len(user_cpl_values)
    print("\n\n--- ♟️ COACHING REPORT (10 Games) ---")
    print(f"Overall Accuracy Score (ACPL): **{avg_cpl:.2f} cp** (This places you in the Beginner range >100 cp)")
    
    # 2. Phase Analysis
    print("\n### Weakness by Game Phase")
    
    # Find the phase with the highest ACPL
    highest_cpl_phase = None
    max_acpl = -1
    
    phase_stats = aggregated_data['phase_cpl_sum']
    
    for phase, stats in phase_stats.items():
        if stats['count'] > 0:
            phase_acpl = stats['cpl'] / stats['count']
            print(f"- {phase} ACPL: {phase_acpl:.2f} cp ({stats['count']} moves)")
            
            if phase_acpl > max_acpl:
                max_acpl = phase_acpl
                highest_cpl_phase = phase
    
    if highest_cpl_phase:
        print(f"\n💡 **Primary Focus:** Your highest error rate occurred in the **{highest_cpl_phase}** phase.")
    
    # 3. Mistake Count Summary
    print("\n### Mistake Breakdown")
    total_mistakes = 0
    
    # Find the most common specific mistake type
    mistake_counts = aggregated_data['mistakes_by_phase']
    
    if mistake_counts:
        most_common_error = max(mistake_counts.items(), key=lambda item: item[1], default=("None", 0))
        
        if most_common_error[1] > 0:
            print(f"**Actionable Insight:** The most frequent error was a **{most_common_error[0]}**.")
            print("---")
            for error, count in mistake_counts.items():
                total_mistakes += count
                print(f"- {error}: {count} times")
            print(f"\n**Total Blunders/Mistakes/Inaccuracies:** {total_mistakes}")

# --- MAIN EXECUTION (Updated to call generate_report) ---

try:
    # Initialize the Stockfish Engine
    print(f"Initializing Stockfish at depth {ANALYSIS_DEPTH}...")
    # FIX APPLIED HERE: Ensure you have made this change!
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) 

    # Lichess Data Fetcher Setup
    session = requests.Session() 
    client = berserk.Client(session=session)
    
    print(f"Fetching last {NUM_GAMES} games for {USERNAME}...\n")
    games = client.games.export_by_player(
        USERNAME, 
        max=NUM_GAMES, 
        as_pgn=True
    )
    
    all_cpl_results = []
    
    for game_pgn in games:
        result = analyze_game(game_pgn, engine)
        all_cpl_results.append(result)

    # 1. Aggregate the 10 games into one summary report
    final_summary = aggregate_all_games(all_cpl_results)

    # 2. Call the reporting function with the combined data
    generate_report(final_summary)
        
except Exception as e:
    print(f"\nAN ERROR OCCURRED: {e}")
finally:
    if 'engine' in locals():
        engine.quit()