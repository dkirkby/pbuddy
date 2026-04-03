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
    const teamScore = this._score[team]
    const [rightPos, leftPos] = this._sideIndicesForTeam(team)
    const firstServerPos = (teamScore % 2 === 0) ? rightPos : leftPos
    if (this._openingTurn || this._servingPlayer === 1) {
      return firstServerPos
    }
    return firstServerPos === rightPos ? leftPos : rightPos
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
      return
    }
    if (this._servingPlayer === 1) {
      this._servingPlayer = 2
    } else {
      this._servingTeam = 1 - this._servingTeam
      this._servingPlayer = 1
    }
  }

  toString(): string {
    const serverScore = this._servingTeam === 0 ? this._score[0] : this._score[1]
    const receiverScore = this._servingTeam === 0 ? this._score[1] : this._score[0]
    return `${serverScore}-${receiverScore}-${this._servingPlayer}`
  }
}
