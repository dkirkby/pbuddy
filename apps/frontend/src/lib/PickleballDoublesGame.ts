export class PickleballDoublesGame {
  /**
   * Track the state of a standard doubles pickleball game.
   *
   * Constructor arguments are the player names in this order:
   *   1. Right-hand player on team A at the start of the game
   *      (and the player who serves first)
   *   2. Left-hand player on team A at the start of the game
   *   3. Right-hand player on team B at the start of the game
   *      (and the player who receives first)
   *   4. Left-hand player on team B at the start of the game
   */
  private _teams: [[string, string], [string, string]]
  private _score: [number, number]
  private _servingTeam: number
  private _servingPlayer: number
  private _openingTurn: boolean
  private _rightIndex: [number, number]
  // Which teams[team] index is "player 1" for the current service turn.
  // Set to rightIndex[team] at each side-out — the player on the RH side starts serving.
  private _player1Index: [number, number]

  constructor(
    teamARightFirstServer: string,
    teamALeftFirstServer: string,
    teamBRightFirstReceiver: string,
    teamBLeftFirstReceiver: string,
  ) {
    this._teams = [
      [teamARightFirstServer, teamALeftFirstServer],
      [teamBRightFirstReceiver, teamBLeftFirstReceiver],
    ]
    this._score = [0, 0]
    this._servingTeam = 0
    this._servingPlayer = 2
    this._openingTurn = true
    this._rightIndex = [0, 0]
    this._player1Index = [0, 0]
  }

  get score(): [number, number] {
    return [this._score[0], this._score[1]]
  }

  get servingTeam(): number {
    return this._servingTeam
  }

  get servingPlayer(): number {
    return this._servingPlayer
  }

  get positions(): [string, string, string, string] {
    const aRight = this._teams[0][this._rightIndex[0]]
    const aLeft = this._teams[0][1 - this._rightIndex[0]]
    const bRight = this._teams[1][this._rightIndex[1]]
    const bLeft = this._teams[1][1 - this._rightIndex[1]]
    return [aRight, aLeft, bRight, bLeft]
  }

  private _sideIndicesForTeam(team: number): [number, number] {
    return team === 0 ? [0, 1] : [2, 3]
  }

  get serverPosition(): number {
    const team = this._servingTeam
    const [rightPos, leftPos] = this._sideIndicesForTeam(team)
    // Player 1 is whoever was on the RH side when this service turn started.
    // Track their current position via rightIndex vs the snapshot taken at side-out.
    const player1Pos = this._rightIndex[team] === this._player1Index[team] ? rightPos : leftPos
    if (this._openingTurn || this._servingPlayer === 1) {
      return player1Pos
    }
    return player1Pos === rightPos ? leftPos : rightPos
  }

  get receiverPosition(): number {
    const receivingTeam = 1 - this._servingTeam
    const [recvRightPos, recvLeftPos] = this._sideIndicesForTeam(receivingTeam)
    const serverIsRight = (this.serverPosition === 0 || this.serverPosition === 2)
    return serverIsRight ? recvRightPos : recvLeftPos
  }

  update(servingTeamWinsRally: boolean): void {
    if (servingTeamWinsRally) {
      const team = this._servingTeam
      this._score[team] += 1
      this._rightIndex[team] = 1 - this._rightIndex[team]
      return
    }
    if (this._openingTurn) {
      this._openingTurn = false
      this._servingTeam = 1
      this._servingPlayer = 1
      // Snapshot: player 1 for team B is whoever is currently on the RH side.
      this._player1Index[1] = this._rightIndex[1]
      return
    }
    if (this._servingPlayer === 1) {
      this._servingPlayer = 2
    } else {
      this._servingTeam = 1 - this._servingTeam
      this._servingPlayer = 1
      // Snapshot: player 1 for the new serving team is whoever is on the RH side now.
      this._player1Index[this._servingTeam] = this._rightIndex[this._servingTeam]
    }
  }

  toString(): string {
    const serverScore = this._servingTeam === 0 ? this._score[0] : this._score[1]
    const receiverScore = this._servingTeam === 0 ? this._score[1] : this._score[0]
    return `${serverScore}-${receiverScore}-${this._servingPlayer}`
  }
}
